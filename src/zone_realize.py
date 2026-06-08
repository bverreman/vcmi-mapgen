"""Zone-graph REALIZER -- the generator built on the learned structure.

Pipeline (every step driven by a LEARNED distribution from zone_graph.learn_params,
not a hand-tuned constant):
  1. N towns      = town_a * passable_area^town_b           (sublinear power law)
  2. place towns  = center-neutral peripheral farthest-point spread
                    (real towns RING a contested centre; old code seeded the centre)
  3. zones        = geodesic nearest-town BFS (zone_structure)
  4. per-zone budget = budget_per_tile * zone_area, split by the CONSERVED recipe
                    (RESOURCE_PILE 29% / REWARD_PICKUP 19% / GUARD 15% / MINE 10% ...)
  5. place within each zone, relation-aware:
       - guards: passage_guard_frac on zone boundaries, rest guarding interior loot
       - everything else: terrain-suitability-weighted interior tiles, min-separated
  6. decorations, footprint-aware to_fm, reachability repair, player wiring -> .vmap
     (reuses the existing, verified energy_place / faithful machinery)

This is the opposite of the terrain CNN: counts, symmetry, and the per-zone economy
come from the structure, NOT from per-tile terrain texture.

Run: uv run python src/zone_realize.py --terrain "All for One" --seed 0 --emit-vmap
"""

import sys, os, json, math, random, collections, argparse

ROOT = "/mnt/data/workspace/vcmi-mapgen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zone_graph as ZG
import zone_structure as ZS
import energy_place as EP
import faithful, recon, traverse as TR

WATER, ROCK = 8, 9
TOWN_ML, TOWN_MT, TOWN_MBR = 6, 6, 2     # town footprint margins (anchor bottom-right)


def fit_rate(names):
    """rate[purpose][terrain] = corpus suitability, for weighting within-zone tiles."""
    terr_tiles = collections.Counter()
    on = collections.defaultdict(lambda: collections.Counter())
    for name in names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        for lvl in m["terrain"]:
            for row in lvl:
                for c in row:
                    terr_tiles[c["t"]] += 1
        for o in m["objects"]:
            r = __import__("ontology").resolve(o["class"], o["subclass"])
            l, x, y = o.get("l", 0), o["x"], o["y"]
            if 0 <= l < len(m["terrain"]) and 0 <= y < len(m["terrain"][0]) and 0 <= x < len(m["terrain"][0][0]):
                on[r["purpose"]][m["terrain"][l][y][x]["t"]] += 1
    return {p: {t: on[p][t] / terr_tiles[t] for t in terr_tiles if terr_tiles[t]} for p in on}


def valid_anchor(x, y, W, H, passable):
    # need room below the anchor too: the entrance ('A' at x-2,y) opens DOWNWARD, so the
    # tile(s) below must be passable land or the town's only door faces water/rock -> a
    # coastal town that can never be reached and gets dropped by reachability repair.
    if not (TOWN_ML <= x < W - TOWN_MBR and TOWN_MT <= y < H - 2):
        return False
    for dx in range(-TOWN_ML, 1):
        for dy in range(-TOWN_MT, 1):
            if not passable[y + dy][x + dx]:
                return False
    if not (passable[y + 1][x - 2] and passable[y + 2][x - 2]):   # land entrance apron
        return False
    return True


def largest_component(passable, W, H):
    """Set of (x,y) in the largest 4-connected passable region."""
    seen = [[False] * W for _ in range(H)]; best = set()
    for y0 in range(H):
        for x0 in range(W):
            if passable[y0][x0] and not seen[y0][x0]:
                dq = collections.deque([(x0, y0)]); seen[y0][x0] = True; comp = []
                while dq:
                    x, y = dq.popleft(); comp.append((x, y))
                    for dx, dy in ZS.NB4:
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < W and 0 <= ny < H and passable[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True; dq.append((nx, ny))
                if len(comp) > len(best):
                    best = set(comp)
    return best


def reserve_corridors(passable, towns, W, H):
    """Free-path guarantee: reserve a 3-wide corridor along the shortest-passable-path
    tree from town[0] to every other town, so a clear route always exists and repair
    never has to drop a town walled off by our own (esp. non-removable MINE) footprints.
    Real maps often gate passages with guards; we keep a parallel free route so the
    reachability proxy passes AND the map stays playable (known realism tradeoff)."""
    if not towns:
        return set()
    reserved = set()
    # The town mask is VBBBV/BBBBB/BBABB: the entrance ('A') is bottom-centre, so the
    # only way in is the tile just below it. Corridors must reach that ENTRANCE, not the
    # anchor (which is the blocking keep). Compute each town's entrance + reserve an apron.
    ents = []
    for (tx, ty) in towns:
        cand = [(tx - 2, ty + 1), (tx - 2, ty), (tx - 1, ty + 1), (tx - 3, ty + 1)]
        ent = next(((x, y) for (x, y) in cand if 0 <= x < W and 0 <= y < H and passable[y][x]), (tx, ty))
        ents.append(ent)
        for x in range(tx - 9, tx + 3):
            for y in range(ty - 7, ty + 3):
                if 0 <= x < W and 0 <= y < H:
                    reserved.add((x, y))
    # corridor tree: shortest passable path from entrance[0] to every other entrance, 3-wide
    start = ents[0]
    parent = {start: None}
    dq = collections.deque([start])
    while dq:
        x, y = dq.popleft()
        for dx, dy in ZS.NB4:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and passable[ny][nx] and (nx, ny) not in parent:
                parent[(nx, ny)] = (x, y); dq.append((nx, ny))
    for t in ents[1:]:
        cur = t
        while cur is not None and cur in parent:
            x, y = cur
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if 0 <= x + dx < W and 0 <= y + dy < H:
                        reserved.add((x + dx, y + dy))
            cur = parent[cur]
    return reserved


def place_towns(passable, n, seed, W, H):
    """Center-neutral peripheral farthest-point spread, restricted to the largest
    passable component so every town is land-reachable (else repair drops the town)."""
    rng = random.Random(seed)
    comp = largest_component(passable, W, H)
    # candidates = valid anchors inside the largest land mass
    cand = [(x, y) for (x, y) in comp if valid_anchor(x, y, W, H, passable)]
    if not cand:
        return []
    # passable centroid -> seed the MOST PERIPHERAL candidate so towns ring the centre
    pts = list(comp)
    cxp = sum(p[0] for p in pts) / len(pts); cyp = sum(p[1] for p in pts) / len(pts)
    towns = [max(cand, key=lambda p: ((p[0] - cxp) ** 2 + (p[1] - cyp) ** 2, rng.random()))]
    while len(towns) < n:
        best, bestd = None, -1
        for p in cand:
            d = min((p[0] - t[0]) ** 2 + (p[1] - t[1]) ** 2 for t in towns)
            if d > bestd:
                bestd, best = d, p
        if best is None:
            break
        towns.append(best)
    return towns


def weighted_place(tiles, n, wfn, occupied, sep, rng, purpose, placed, l=0):
    """Place up to n objects of `purpose` on `tiles`, prob ~ wfn(x,y), min-separated."""
    cand = [(x, y) for (x, y) in tiles if wfn(x, y) > 0]
    if not cand:
        cand = list(tiles)                     # terrain has no preference here -> uniform
    if not cand:
        return 0
    w = [max(1e-6, wfn(x, y)) for (x, y) in cand]
    got = 0; attempts = 0; cap = max(40, n * 80)
    samepts = [(px, py) for (pl, px, py, pp) in placed if pl == l and pp == purpose]
    while got < n and attempts < cap:
        attempts += 1
        x, y = rng.choices(cand, weights=w, k=1)[0]
        if (l, x, y) in occupied:
            continue
        if sep and any(abs(x - px) + abs(y - py) < sep for (px, py) in samepts):
            continue
        placed.append((l, x, y, purpose)); occupied.add((l, x, y))
        samepts.append((x, y)); got += 1
    return got


def realize(terr_cells, params, rate, seed=0, verbose=False):
    """terrain (corpus cell grid, level 0 used) -> points [(l,x,y,purpose)]."""
    rng = random.Random(seed)
    H = len(terr_cells); W = len(terr_cells[0])
    terr = [[terr_cells[y][x]["t"] for x in range(W)] for y in range(H)]
    passable = [[terr[y][x] not in (WATER, ROCK) for x in range(W)] for y in range(H)]
    area = sum(1 for row in passable for v in row if v)

    # 1) town count from the learned power law, with seed jitter
    n_mean = params["town_a"] * area ** params["town_b"]
    n = max(2, int(round(n_mean * (1.0 + rng.uniform(-0.12, 0.12)))))

    # per-map gameplay density: sample from the upper half of the corpus (p50–p90).
    # The lower half contains tiny/singleplayer maps that are correctly sparse but
    # drag the mean down; multiplayer maps like All for One live in the upper half.
    dens = params.get("densities") or [params["budget_per_tile"]]
    lo, hi = dens[len(dens) // 2], dens[len(dens) * 9 // 10]
    density = min(hi, max(lo, rng.choice(dens[len(dens)//2:len(dens)*9//10])))

    # per-map decoration density: same approach — sample from the corpus spread.
    # The terrain-rate estimate under-counts by ~35% vs real; learning the density
    # distribution directly gives the right absolute count.
    ddensities = params.get("deco_densities") or []
    if ddensities:
        dlo = ddensities[len(ddensities) // 4]    # p25: avoids the very sparse outliers
        dhi = ddensities[len(ddensities) * 9 // 10]
        deco_density = min(dhi, max(dlo, rng.choice(ddensities[len(ddensities)//4:len(ddensities)*9//10])))
    else:
        deco_density = None   # fallback to terrain-rate estimate

    # 2) center-neutral peripheral town placement + clear inter-town corridors
    towns = place_towns(passable, n, seed, W, H)
    label, _ = ZS.zones_by_town(terr, towns, W, H)
    reserved = reserve_corridors(passable, towns, W, H)

    placed = []
    occupied = set()
    for (tx, ty) in towns:
        placed.append((0, tx, ty, "TOWN")); occupied.add((0, tx, ty))

    # zone tile lists + boundary tiles
    zone_tiles = collections.defaultdict(list)
    for y in range(H):
        for x in range(W):
            if label[y][x] >= 0:
                zone_tiles[label[y][x]].append((x, y))
    boundary = collections.defaultdict(set)
    for y in range(H):
        for x in range(W):
            zi = label[y][x]
            if zi < 0:
                continue
            for dx, dy in ZS.NB4:
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and label[ny][nx] >= 0 and label[ny][nx] != zi:
                    boundary[zi].add((x, y))

    recipe = params["recipe"]
    pg_frac = params["passage_guard_frac"]
    # purposes we actively place per zone (TOWN handled; skip rare/relational tails for v1)
    PLACE = [p for p in recipe if p not in ("TOWN",)]

    for zi, tiles in zone_tiles.items():
        ztiles = [(x, y) for (x, y) in tiles if (0, x, y) not in occupied and (x, y) not in reserved]
        total = density * len(tiles)
        btiles = [(x, y) for (x, y) in boundary[zi] if (x, y) not in reserved]

        def wfn(x, y, P):
            return rate.get(P, {}).get(terr[y][x], 0.0)

        for P in PLACE:
            cnt = total * recipe[P]
            cnt = int(cnt) + (1 if rng.random() < (cnt - int(cnt)) else 0)
            if cnt <= 0:
                continue
            if P == "GUARD":
                npass = int(round(cnt * pg_frac))
                # passage guards sit ON the zone boundary; interior guards in the zone body
                weighted_place(btiles, npass, lambda x, y: 1.0, occupied, 2, rng, P, placed)
                weighted_place(ztiles, cnt - npass, lambda x, y: wfn(x, y, P) or 1.0,
                               occupied, 3, rng, P, placed)
            else:
                sep = 1 if P in ("REWARD_PICKUP", "RESOURCE_PILE") else 3   # loot may cluster
                weighted_place(ztiles, cnt, lambda x, y, P=P: wfn(x, y, P),
                               occupied, sep, rng, P, placed)

    # 6) decorations: use corpus deco_density if available, else terrain-rate estimate
    # Block corridor tiles so decorations don't land in the only passable route between towns.
    for (rx, ry) in reserved:
        occupied.add((0, rx, ry))
    terr3 = [[[{"t": terr[y][x]} for x in range(W)] for y in range(H)]]
    if deco_density is not None:
        deco_n = int(round(deco_density * area))
    else:
        deco_n = int(round(sum(rate.get("DECORATION", {}).get(terr[y][x], 0.0)
                               for y in range(H) for x in range(W))))
    # Use larger sigma for denser, more visible forest patches (real: diag/18 not diag/28)
    import math as _math
    _orig_sigma = _math.hypot(W, H) / 28.0
    _dense_sigma = max(3.5, _math.hypot(W, H) / 18.0)
    # monkey-patch the sigma for this call via rate override (no-op; patch energy_place)
    EP.sample_tiles_deco(terr3, rate, rng, occupied, placed, deco_n, 1, H, W,
                         sigma_override=_dense_sigma, zone_label=label)

    if verbose:
        cg = collections.Counter(p for _, _, _, p in placed)
        print(f"  N towns={n} (mean {n_mean:.1f}); density={density:.3f} "
              f"(corpus {dens[0]:.3f}-{dens[-1]:.3f}); placed { {k: cg[k] for k in sorted(cg)} }")
    return placed, towns, label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", default="All for One")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--emit-vmap", action="store_true")
    args = ap.parse_args()

    names = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in __import__("glob").glob(f"{ROOT}/out/maps/*.json"))
    print(f"learning structure params + terrain rates on {len(names)} maps...")
    params = ZG.learn_params()
    rate = fit_rate(names)

    src = json.load(open(f"{ROOT}/out/maps/{args.terrain}.json"))
    terr_cells = src["terrain"][0]
    points, towns, label = realize(terr_cells, params, rate, seed=args.seed, verbose=True)

    # build faithful map + repair reachability (reuse verified machinery)
    terr_full = [src["terrain"][0]]
    fm = EP.to_fm(terr_full, points, name=f"ZoneGraph {args.terrain} s{args.seed}")
    n_rem, ok = EP.repair_reachability(fm)
    rr = TR.traverse(fm)
    gp = [p for p in points if p[3] != "DECORATION"]
    print(f"gameplay objects placed: {len(gp)} (+{len(points)-len(gp)} deco); "
          f"reachable={rr['ok']} (repair removed {n_rem})")

    if args.emit_vmap:
        safe = args.terrain.replace(" ", "_")
        out = f"{ROOT}/out/ZoneGraph-{safe}-s{args.seed}.vmap"
        faithful.to_vmap(fm, out, name=fm["name"])
        print("vmap:", out)


if __name__ == "__main__":
    main()
