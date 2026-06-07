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
    # relational couplings from the coherence model: signed strength per (P -> Q)
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


def place(terr, model, seed=0, kA=2.5, radius_frac=0.16, min_sep=3):
    """Sequential point-process placement on the given terrain. Returns
    [(l,x,y,purpose)] for gameplay + decoration objects."""
    rng = random.Random(seed)
    rate, coupling = model["rate"], model["coupling"]
    L = len(terr); H = len(terr[0]); W = len(terr[0][0])
    R = radius_frac * math.hypot(W, H)
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
        for _ in range(n):
            # sample K random tiles, pick one proportional to intensity (A-Res reservoir)
            best = None; acc = 0.0
            K = 600
            for _ in range(K):
                l = rng.randrange(L); y = rng.randrange(H); x = rng.randrange(W)
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


def sample_tiles_deco(terr, rate, rng, occupied, placed, n, L, H, W):
    for _ in range(max(0, n)):
        for _try in range(8):
            l = rng.randrange(L); y = rng.randrange(H); x = rng.randrange(W)
            t = terr[l][y][x]["t"]
            base = rate.get("DECORATION", {}).get(t, 0.0)
            if base <= 0 or (l, x, y) in occupied:
                continue
            if rng.random() < min(1.0, base * 6):
                placed.append((l, x, y, "DECORATION"))
                occupied.add((l, x, y))
                break


def to_fm(terr, points, name):
    """[(l,x,y,purpose)] + terrain -> faithful fm (concrete objlib objects)."""
    wterr = [[[recon._terr_cell(c) for c in row] for row in lvl] for lvl in terr]
    objs = []; main_town = None
    for (l, x, y, P) in points:
        e = recon.pick_entry(P, terr[l][y][x]["t"])
        if not e:
            continue
        objs.append({"type": e["type"], "subtype": e["subtype"],
                     "animation": e["animation"], "mask": e["mask"], "x": x, "y": y, "l": l})
        if P == "TOWN" and l == 0 and main_town is None:
            main_town = {"l": 0, "x": x, "y": y}
    return {"terrain": wterr, "objects": objs, "main_town": main_town, "name": name}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", default="All for One", help="corpus map whose terrain to place on")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--emit-vmap", action="store_true")
    args = ap.parse_args()

    names = CO.all_map_names()
    print(f"fitting intensity + couplings on {len(names)} maps...")
    model = fit(names)

    src = json.load(open(f"{ROOT}/out/maps/{args.terrain}.json"))
    terr = src["terrain"]
    real_pts = CO.objects_from_map(src)

    pts = place(terr, model, seed=args.seed)
    gp = [(l, x, y, p) for (l, x, y, p) in pts if p in CO.PURPOSES]

    comodel = CO.fit(names[::2])
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
    rr = TR.traverse(fm)
    print(f"reachable: {rr['ok']}")

    if args.emit_vmap:
        safe = args.terrain.replace(" ", "_")
        out = f"{ROOT}/out/MethodA-{safe}-s{args.seed}.vmap"
        faithful.to_vmap(fm, out, name=fm["name"])
        print("vmap:", out)


if __name__ == "__main__":
    main()
