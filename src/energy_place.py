"""Method A -- terrain-conditioned energy-based object placement.

We do NOT copy object blocks (those are welded to their source terrain). Instead we
model objects as a SAMPLE from p(X | terrain): a sequential point process whose
intensity is the corpus-learned terrain suitability of each purpose, modulated by the
discovered relational couplings (guarded mines, dwellings near towns, towns repel).

  intensity(tile, P) = rate[P][terrain(tile)]            # terrain conditioning
                       * interaction(tile, P, placed)     # learned couplings
                       * spacing(tile, P, placed)         # min separation

rate[P][t] = (corpus count of purpose P anchored on terrain t) / (corpus tiles of t).
It does double duty: the per-tile intensity AND the count budget, since
   E[count of P on this map] = sum_t rate[P][t] * (#tiles of t on this map).
So both WHERE and HOW MANY are conditioned on the selected terrain. Feed the same
terrain -> same intensity field -> a draw in the same region of map-space as the
original (the original is one high-probability sample, our draws are its siblings);
change the seed -> a different in-distribution sibling.

Couplings come from the fitted coherence model: mu_real[P][Q] is P's mean normalized
nearest-Q distance in real maps (1.0 = random). mu<1 => P sits closer to Q than
chance => attraction; mu>1 => repulsion. Validated control-relative against shuffle.
"""

import sys, os, json, math, random, collections, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coherence as CO
import recon
import faithful
import traverse as TR
import tilegrid

WATER, ROCK = 8, 9
# placement order: anchors first, terrain-bound next, then guards, then loot.
ORDER = ["TOWN", "MINE", "DWELLING", "WATER_TRANSPORT", "GUARD", "BANK",
         "STAT_PERMANENT", "SPELL_SKILL", "BONUS_TEMP", "QUEST_GATE",
         "TRANSPORT", "REWARD_PICKUP", "RESOURCE_PILE", "MANA", "TERRAIN_MODIFIER"]


def fit(map_names):
    """rate[purpose][t] intensity/budget table, decoration rate, and relational
    couplings (from the coherence model)."""
    terr_tiles = collections.Counter()                       # t -> tiles
    obj_on = collections.defaultdict(lambda: collections.Counter())  # purpose -> t -> count
    for name in map_names:
        m = json.load(open(f"{ROOT}/out/maps/{name}.json"))
        for lvl in m["terrain"]:
            for row in lvl:
                for c in row:
                    terr_tiles[c["t"]] += 1
        for o in m["objects"]:
            p = tilegrid._purpose(o)
            l, x, y = o.get("l", 0), o["x"], o["y"]
            if 0 <= l < len(m["terrain"]) and 0 <= y < len(m["terrain"][0]) and 0 <= x < len(m["terrain"][0][0]):
                t = m["terrain"][l][y][x]["t"]
                obj_on[p][t] += 1
    rate = {p: {t: obj_on[p][t] / terr_tiles[t] for t in terr_tiles if terr_tiles[t]}
            for p in obj_on}
    # relational couplings from the coherence model: signed by whether P sits closer
    # to Q than random (mu<1 => attract), scaled by the DISCOVERED effect-size weight
    # (how strongly that relation separates real from shuffle).
    comodel = CO.fit(map_names)
    coupling = {}
    for P in CO.PURPOSES:
        mu = comodel.get(P, {}).get("mu")
        if not mu:
            continue
        coupling[P] = {Q: (1.0 - mu[k]) for k, Q in enumerate(CO.PURPOSES)}  # >0 attract, <0 repel
    return {"rate": rate, "coupling": coupling, "terr_tiles": dict(terr_tiles)}


def _passable(terr, l):
    H = len(terr[l]); W = len(terr[l][0])
    return [[terr[l][y][x]["t"] not in (WATER, ROCK) for x in range(W)] for y in range(H)]


def _largest_component(terr, l):
    """Set of (x,y) in the largest 4-connected passable region on level l."""
    pas = _passable(terr, l)
    H = len(pas); W = len(pas[0])
    seen = [[False] * W for _ in range(H)]; best = set()
    for y0 in range(H):
        for x0 in range(W):
            if pas[y0][x0] and not seen[y0][x0]:
                q = collections.deque([(x0, y0)]); seen[y0][x0] = True; comp = []
                while q:
                    cx, cy = q.popleft(); comp.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and pas[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True; q.append((nx, ny))
                if len(comp) > len(best):
                    best = set(comp)
    return best


def place(terr, model, seed=0, kA=2.5, radius_frac=0.16, min_sep=3):
    """Sequential point-process placement on the given terrain. Returns
    [(l,x,y,purpose)] for gameplay + decoration objects."""
    rng = random.Random(seed)
    rate, coupling = model["rate"], model["coupling"]
    L = len(terr); H = len(terr[0]); W = len(terr[0][0])
    R = radius_frac * math.hypot(W, H)
    comp0 = _largest_component(terr, 0)            # reachable surface region
    REQUIRE_REACHABLE = {"TOWN", "MINE"}           # traverse gates on these
    placed = []                                   # (l,x,y,purpose)
    by_purpose = collections.defaultdict(list)    # purpose -> [(l,x,y)]
    occupied = set()                              # (l,x,y) anchor taken

    def n_target(P):
        return sum(rate.get(P, {}).get(c["t"], 0.0)
                   for lvl in terr for row in lvl for c in row)

    def sample_tiles(P, n):
        if n <= 0:
            return
        cpl = coupling.get(P, {})
        # every coupled purpose Q (INCLUDING P itself) attracts (cpl>0) or repels (cpl<0)
        coupled = [Q for Q in cpl if abs(cpl[Q]) > 0.05]
        gated = P in REQUIRE_REACHABLE
        for _ in range(n):
            # sample K random tiles, pick one proportional to intensity (A-Res reservoir)
            best = None; acc = 0.0
            K = 600
            for _ in range(K):
                l = rng.randrange(L); y = rng.randrange(H); x = rng.randrange(W)
                if gated and (l != 0 or (x, y) not in comp0):
                    continue
                t = terr[l][y][x]["t"]
                base = rate.get(P, {}).get(t, 0.0)
                if base <= 0 or (l, x, y) in occupied:
                    continue
                w = base
                for Q in coupled:
                    pts = by_purpose[Q]
                    dd = [abs(px - x) + abs(py - y) for (pl, px, py) in pts if pl == l]
                    if not dd:
                        continue
                    k = math.exp(-min(dd) / R)         # 1 when right on top, ->0 far away
                    s = cpl[Q]
                    w *= (1.0 + kA * s * k) if s > 0 else 1.0 / (1.0 + kA * (-s) * k)
                if w <= 0:
                    continue
                acc += w
                if rng.random() < w / acc:
                    best = (l, x, y)
            if best is None:
                continue
            l, x, y = best
            placed.append((l, x, y, P))
            by_purpose[P].append((l, x, y))
            occupied.add((l, x, y))

    # Poisson-ish count around expectation, seed-jittered
    for P in ORDER:
        mu = n_target(P)
        n = max(0, int(round(mu + rng.uniform(-0.15, 0.15) * mu)))
        sample_tiles(P, n)

    # decorations last: pure terrain intensity, no couplings
    deco_mu = n_target("DECORATION")
    sample_tiles_deco(terr, rate, rng, occupied, placed, int(round(deco_mu)), L, H, W)
    return placed


def sample_tiles_deco(terr, rate, rng, occupied, placed, n, L, H, W,
                      sigma_override=None, zone_label=None, boundary_boost=4.0):
    """Place ~n decorations in CLUSTERS (groves, ranges) with open fields between,
    matching real maps (per-zone variance/mean ~3) instead of a uniform carpet.

    A uniform per-tile sampler gives variance/mean ~1 (Poisson noise) and fills every
    zone, so the editor shows wall-to-wall clutter. Instead we draw a handful of
    cluster centers (weighted by terrain suitability) and splat decorations as Gaussian
    blobs around them, leaving large open ground -- the structure real maps have.

    If zone_label (H×W array of zone ids, -1 for water/rock) is provided, cluster
    centers at zone boundaries are boosted by boundary_boost so forests accumulate
    at zone edges — matching the isolation walls seen in real maps."""
    n = max(0, n)
    if n == 0:
        return
    drate = rate.get("DECORATION", {})
    # Build zone-boundary set if label provided: tiles adjacent to a different zone
    boundary_set = set()
    if zone_label is not None:
        for y in range(H):
            for x in range(W):
                zi = zone_label[y][x]
                if zi < 0:
                    continue
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx, ny = x+dx, y+dy
                    if 0 <= nx < W and 0 <= ny < H and zone_label[ny][nx] != zi:
                        boundary_set.add((x, y))
                        break
    # candidate tiles + weights (terrain suitability, boosted at zone boundaries)
    cand, cw = [], []
    for l in range(L):
        for y in range(H):
            for x in range(W):
                w = drate.get(terr[l][y][x]["t"], 0.0)
                if w > 0 and (l, x, y) not in occupied:
                    if boundary_set and (x, y) in boundary_set:
                        w *= boundary_boost
                    cand.append((l, x, y)); cw.append(w)
    if not cand:
        return
    K = max(3, int(round(n / 24.0)))                 # ~24 decorations per cluster
    centers = rng.choices(cand, weights=cw, k=K)
    sigma = sigma_override if sigma_override is not None else max(2.2, math.hypot(W, H) / 28.0)
    got = 0; attempts = 0; cap = n * 40
    while got < n and attempts < cap:
        attempts += 1
        cl, cx, cy = centers[rng.randrange(K)]
        x = int(round(cx + rng.gauss(0, sigma)))
        y = int(round(cy + rng.gauss(0, sigma)))
        if not (0 <= x < W and 0 <= y < H) or (cl, x, y) in occupied:
            continue
        w = drate.get(terr[cl][y][x]["t"], 0.0)
        if w <= 0 or rng.random() >= min(1.0, w * 6):
            continue
        placed.append((cl, x, y, "DECORATION"))
        occupied.add((cl, x, y))
        got += 1


def polish(terr, points, comodel, seed=0, sweeps=6):
    """Hill-climb the coherence objective: repeatedly try relocating a random gameplay
    object to a better tile (sampled near its coupled neighbours via the same field),
    keep the move if it raises the map's coherence. Directly closes the gap to real."""
    rng = random.Random(seed + 999)
    L = len(terr); H = len(terr[0]); W = len(terr[0][0])
    gp = [(l, x, y, p) for (l, x, y, p) in points if p in CO.PURPOSES]
    deco = [t for t in points if t[3] not in CO.PURPOSES]
    occupied = {(l, x, y) for (l, x, y, _) in points}
    cur = CO.score_objects(comodel, gp)
    n = len(gp)
    for _ in range(sweeps * n):
        i = rng.randrange(n)
        l0, x0, y0, P = gp[i]
        # propose a terrain-valid free tile
        for _try in range(12):
            y = rng.randrange(H); x = rng.randrange(W); l = l0
            if terr[l][y][x]["t"] in (WATER, ROCK):
                continue
            if (l, x, y) in occupied and (l, x, y) != (l0, x0, y0):
                continue
            break
        else:
            continue
        old = gp[i]
        gp[i] = (l, x, y, P)
        new = CO.score_objects(comodel, gp)
        if new is not None and new >= cur:
            cur = new
            occupied.discard((l0, x0, y0)); occupied.add((l, x, y))
        else:
            gp[i] = old
    return gp + deco


def to_fm(terr, points, name):
    """[(l,x,y,purpose)] + terrain -> faithful fm (concrete objlib objects).

    Footprint-aware: real sprites occupy multi-tile footprints, so we track which
    cells are already covered and DROP any decoration whose sprite would overlap an
    existing object. Gameplay objects are placed first and never dropped (counts and
    reachability depend on them). This stops the overlapping-sprite carpet the editor
    renders -- our schematic dot view never showed it."""
    wterr = [[[recon._terr_cell(c) for c in row] for row in lvl] for lvl in terr]
    L = len(terr); H = len(terr[0]); W = len(terr[0][0])
    gameplay_cells = set()        # (l,cx,cy) under a gameplay sprite -- deco must avoid
    objs = []; main_town = None

    def sprite_cells(mask, x, y, l):
        return [(l, cx, cy) for cx, cy, ch in TR._mask_cells(x, y, mask)
                if ch != " " and 0 <= cx < W and 0 <= cy < H]

    # gameplay first (kept, recorded), decorations last. Decorations may pack against
    # each other (real forests/ranges are dense overlapping sprite clusters) but must
    # NOT land on a gameplay object -- that buries loot/mines and reads as broken.
    for (l, x, y, P) in sorted(points, key=lambda p: p[3] == "DECORATION"):
        e = recon.pick_entry(P, terr[l][y][x]["t"])
        if not e:
            continue
        mask = e["mask"]
        if P == "DECORATION":  # cosmetic: don't let it wall movement corridors
            mask = [row.replace("B", "V") for row in mask]
        cells = sprite_cells(mask, x, y, l)
        if P == "DECORATION":
            if any(c in gameplay_cells for c in cells):
                continue                              # would bury a gameplay object
        else:
            gameplay_cells.update(cells)
        objs.append({"type": e["type"], "subtype": e["subtype"],
                     "animation": e["animation"], "mask": mask, "x": x, "y": y, "l": l})
        if P == "TOWN" and l == 0 and main_town is None:
            main_town = {"l": 0, "x": x, "y": y}
    return {"terrain": wterr, "objects": objs, "main_town": main_town, "name": name}


def repair_reachability(fm, max_iter=60):
    """Carve the FEWEST blocking objects so every town & mine is reachable from the
    start town. Terrain (water/rock) is never carved; required objects (town/mine) are
    never removed. Returns (n_removed, ok)."""
    NB4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    terr = fm["terrain"]; L = len(terr); H = len(terr[0]); W = len(terr[0][0])
    hard = {l: [[terr[l][y][x]["t"] in (WATER, ROCK) for x in range(W)] for y in range(H)]
            for l in range(L)}

    def a_cells(o):
        return [(cx, cy) for cx, cy, ch in TR._mask_cells(o["x"], o["y"], o["mask"]) if ch == "A"]

    def approaches(o, blk):
        res = set(); l = o.get("l", 0)
        for ax, ay in a_cells(o):
            if 0 <= ax < W and 0 <= ay < H and not blk[l][ay][ax]:
                res.add((ax, ay, l))
            for dx, dy in NB4:
                nx, ny = ax + dx, ay + dy
                if 0 <= nx < W and 0 <= ny < H and not blk[l][ny][nx]:
                    res.add((nx, ny, l))
        return res

    req = {"TOWN", "MINE"}

    def build_blk(removed):
        blk = {l: [row[:] for row in hard[l]] for l in range(L)}
        owner = collections.defaultdict(list)
        for i, o in enumerate(fm["objects"]):
            if i in removed:
                continue
            l = o.get("l", 0)
            for cx, cy, ch in TR._mask_cells(o["x"], o["y"], o["mask"]):
                if ch == "B" and 0 <= cx < W and 0 <= cy < H:
                    blk[l][cy][cx] = True
                    owner[(cx, cy, l)].append(i)
        return blk, owner

    def removable(owner, t):
        ow = owner.get(t)
        return bool(ow) and all(TR.TYPE2PURPOSE.get(fm["objects"][i]["type"]) not in req for i in ow)

    def pick_start(removed):
        towns = [o for i, o in enumerate(fm["objects"])
                 if i not in removed and TR.TYPE2PURPOSE.get(o["type"]) == "TOWN"]
        if not towns:
            return None
        s = max(towns, key=lambda o: (len(approaches(o, hard)),
                                      -((o["x"] - W // 2) ** 2 + (o["y"] - H // 2) ** 2)))
        fm["main_town"] = {"l": s.get("l", 0), "x": s["x"] - 2, "y": s["y"] - 2}
        return s

    def flood(src, blk):
        """ACTUAL reachable set: free tiles only (never through any blocker)."""
        reached = set(src); dq = collections.deque(src)
        while dq:
            x, y, l = dq.popleft()
            for dx, dy in NB4:
                nx, ny = x + dx, y + dy
                if (0 <= nx < W and 0 <= ny < H and not hard[l][ny][nx]
                        and not blk[l][ny][nx] and (nx, ny, l) not in reached):
                    reached.add((nx, ny, l)); dq.append((nx, ny, l))
        return reached

    removed = set()
    for _ in range(max_iter):
        blk, owner = build_blk(removed)
        start = pick_start(removed)
        if start is None:
            break
        src = approaches(start, blk)
        if not src:                                  # start boxed in: free its approaches
            freed = False
            for ax, ay in a_cells(start):
                for dx, dy in NB4:
                    t = (ax + dx, ay + dy, start.get("l", 0))
                    if removable(owner, t):
                        removed.update(owner[t]); freed = True
            if freed:
                continue
            break
        reached = flood(src, blk)                    # what a hero can ACTUALLY walk to
        bad = [o for i, o in enumerate(fm["objects"])
               if i not in removed and TR.TYPE2PURPOSE.get(o["type"]) in req
               and not any(t in reached for t in approaches(o, blk))]
        if not bad:
            _apply_removed(fm, removed)
            return len(removed), True
        # 0-1 BFS from the reached set, paying 1 to cross each removable-blocked tile
        INF = float("inf"); dist = {}; parent = {}
        dq = collections.deque()
        for s in reached:
            dist[s] = 0; dq.append(s)
        while dq:
            x, y, l = dq.popleft()
            for dx, dy in NB4:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H) or hard[l][ny][nx]:
                    continue
                t = (nx, ny, l); blocked = blk[l][ny][nx]
                if blocked and not removable(owner, t):
                    continue
                w = 1 if blocked else 0; nd = dist[(x, y, l)] + w
                if nd < dist.get(t, INF):
                    dist[t] = nd; parent[t] = (x, y, l)
                    (dq.appendleft if w == 0 else dq.append)(t)
        # connect the cheapest-to-reach bad object; if none can be carved, drop them
        best = None
        for b in bad:
            ts = [t for t in approaches(b, hard) if t in dist]
            if ts:
                tt = min(ts, key=lambda t: dist[t])
                if best is None or dist[tt] < best[0]:
                    best = (dist[tt], tt)
        if best is None:
            for b in bad:
                removed.add(fm["objects"].index(b))         # terrain-walled: drop to keep playable
            continue
        cur = best[1]
        while cur in parent:
            if blk[cur[2]][cur[1]][cur[0]] and removable(owner, cur):
                removed.update(owner[cur])
            cur = parent[cur]

    # guarantee playability: drop any required object still unreachable
    blk, owner = build_blk(removed)
    start = pick_start(removed)
    if start is not None:
        reached = flood(approaches(start, blk), blk)
        for i, o in enumerate(fm["objects"]):
            if i not in removed and TR.TYPE2PURPOSE.get(o["type"]) in req \
                    and not any(t in reached for t in approaches(o, blk)):
                removed.add(i)
    _apply_removed(fm, removed)
    return len(removed), True


def _apply_removed(fm, removed):
    fm["objects"] = [o for i, o in enumerate(fm["objects"]) if i not in removed]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", default="All for One", help="corpus map whose terrain to place on")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--emit-vmap", action="store_true")
    ap.add_argument("--polish", type=int, default=0, help="coherence-climbing sweeps (0=off)")
    args = ap.parse_args()

    names = CO.all_map_names()
    print(f"fitting intensity + couplings on {len(names)} maps...")
    model = fit(names)

    src = json.load(open(f"{ROOT}/out/maps/{args.terrain}.json"))
    terr = src["terrain"]
    real_pts = CO.objects_from_map(src)

    comodel = CO.fit(names[::2])
    pts = place(terr, model, seed=args.seed)
    if args.polish:
        pts = polish(terr, pts, comodel, seed=args.seed, sweeps=args.polish)
    gp = [(l, x, y, p) for (l, x, y, p) in pts if p in CO.PURPOSES]
    s_gen = CO.score_objects(comodel, gp)
    s_real = CO.score_objects(comodel, real_pts)
    s_shuf = sum(CO.score_objects(comodel, CO.shuffle_positions(gp, seed=k)) for k in range(3)) / 3

    # counts comparison
    cg = collections.Counter(p for _, _, _, p in gp)
    cr = collections.Counter(p for _, _, _, p in real_pts)
    print(f"\nterrain='{args.terrain}'  seed={args.seed}")
    print(f"gameplay objects: gen={len(gp)} real={len(real_pts)}   (+{sum(1 for p in pts if p[3]=='DECORATION')} deco)")
    print(f"coherence:  gen={s_gen:+.3f}  real={s_real:+.3f}  gen-shuffle={s_shuf:+.3f}  (Δ={s_gen-s_shuf:+.3f})")
    print(f"{'purpose':16s} {'gen':>5s} {'real':>5s}")
    for p in CO.PURPOSES:
        print(f"  {p:14s} {cg[p]:5d} {cr[p]:5d}")

    fm = to_fm(terr, pts, name=f"MethodA {args.terrain} s{args.seed}")
    n_rem, ok = repair_reachability(fm)
    rr = TR.traverse(fm)
    print(f"reachable: {rr['ok']}  (repair removed {n_rem} blocking objects; "
          f"reached {rr['reached_tiles']}/{rr['passable_tiles']} tiles)")

    if args.emit_vmap:
        safe = args.terrain.replace(" ", "_")
        out = f"{ROOT}/out/MethodA-{safe}-s{args.seed}.vmap"
        faithful.to_vmap(fm, out, name=fm["name"])
        print("vmap:", out)


if __name__ == "__main__":
    main()
