"""Macro terrain model (L0) — capacity-constrained zone growth + boundary texturing (spec §4).

The causal 3-tile Markov chain (`markov_terrain`) reproduces border texture but its patch sizes
decay geometrically — generated maps segment into many small fragments, while corpus maps hold a
handful of LARGE designed regions. This module plans the macro structure first:

  1. **mine**   — corpus macro statistics: zone-area distribution, per-terrain area shares,
                  terrain adjacency mix, water fraction (cached in ``data/pp/macro_stats.json``).
  2. **plan**   — sample a water mask (low-frequency noise at the corpus water quantile), draw
                  zone target AREAS from the corpus distribution (scaled to fill the land),
                  spread seeds, and assign terrains by Metropolis on the seed k-NN graph with
                  energy −log A[t_i][t_j] (adjacent zones prefer corpus-frequent terrain pairs;
                  same-terrain contact is corpus-impossible, so it is strongly repelled).
  3. **grow**   — capacity-constrained multi-source Dijkstra with jittered costs: each zone
                  claims tiles by increasing noisy distance until it reaches its target area —
                  the corpus zone-size distribution is imposed BY CONSTRUCTION, with organic
                  (non-Voronoi) borders.
  4. **texture**— the Markov chain demoted to where it is good: isotropic Gibbs sweeps
                  RESTRICTED to a 2-tile band around zone borders (interiors clamped), so
                  boundaries get corpus transition texture and interiors can never fragment.

    uv run python -m vcmi_mapgen.macro_topo --seed 3 --size 72
"""
import argparse
import collections
import heapq
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zone_engine as ZE        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_PATH = os.path.join(ROOT, "data", "pp", "macro_stats.json")
WATER, ROCK = 8, 9
MIN_ZONE_AREA = 40              # floor for sampled target areas
JITTER = 1.4                    # growth-cost noise amplitude (0 = pure Voronoi-like fronts)
BAND = 2                        # boundary-texturing band half-width (tiles)


# ---------------------------------------------------------------------------
# 1. corpus macro statistics
# ---------------------------------------------------------------------------

def mine_macro(force=False):
    import obj_resolve as OR
    if not force and os.path.exists(STATS_PATH):
        return json.load(open(STATS_PATH))
    areas, water_fracs = [], []
    terr_share = collections.Counter()
    adj = collections.Counter()                      # "t1|t2" boundary-tile counts, t1 <= t2
    nzones = []
    for nm in OR.all_map_names():
        try:
            fm = OR.load_faithful(nm)
        except Exception:
            continue
        lvl = fm["terrain"][0]
        H = len(lvl); W = len(lvl[0]) if H else 0
        T = [[c["t"] for c in row] for row in lvl]
        nw = sum(1 for row in T for t in row if t == WATER)
        water_fracs.append(nw / max(W * H, 1))
        zones, zl, _ = ZE._segment_level(lvl)
        big = 0
        for z in zones.values():
            t = z["terrain_type"]
            if 0 <= t < 8:
                areas.append(z["area"])
                terr_share[t] += z["area"]
                if z["area"] >= 60:
                    big += 1
        nzones.append(big)
        for y in range(H):
            for x in range(W):
                a = T[y][x]
                for dx, dy in ((1, 0), (0, 1)):
                    if x + dx < W and y + dy < H:
                        b = T[y + dy][x + dx]
                        if a != b and 0 <= a < 8 and 0 <= b < 8:
                            adj[f"{min(a, b)}|{max(a, b)}"] += 1
    st = {"areas": sorted(areas), "water_fracs": sorted(water_fracs),
          "terr_share": {str(k): v for k, v in terr_share.items()},
          "adj": dict(adj), "nzones": nzones}
    os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
    json.dump(st, open(STATS_PATH, "w"))
    return st


# ---------------------------------------------------------------------------
# 2. plan: water, areas, seeds, terrain assignment
# ---------------------------------------------------------------------------

def _water_mask(W, H, frac, rng, cell=None):
    """Coherent water blobs: value noise thresholded at the `frac` quantile. `cell` sets the
    noise wavelength — large (default) gives seas/lakes, small fragments land into islands."""
    if frac <= 0.01:
        return [[False] * W for _ in range(H)]
    noise = ZE._value_noise(W, H, cell or max(6, min(W, H) // 5), rng)
    flat = sorted(v for row in noise for v in row)
    thr = flat[int(frac * (len(flat) - 1))]
    return [[noise[y][x] <= thr for x in range(W)] for y in range(H)]


def _sample_areas(st, budget, rng):
    """Zone target areas drawn from the corpus area distribution, scaled to fill `budget`."""
    pool = [a for a in st["areas"] if a >= MIN_ZONE_AREA]
    tgt = []
    while sum(tgt) < budget:
        tgt.append(rng.choice(pool))
    s = budget / sum(tgt)
    tgt = [max(MIN_ZONE_AREA, int(round(a * s))) for a in tgt]
    return tgt


def _assign_terrains(seeds, st, rng, iters=400):
    """Corpus-weighted terrains on the seed 3-NN graph via Metropolis on the adjacency energy
    E = sum_edges -log P_adj(t_i, t_j): corpus-frequent terrain pairs attract, same-terrain
    contact (never observed — same terrains merge into one zone) is strongly repelled."""
    n = len(seeds)
    share = {int(k): v for k, v in st["terr_share"].items()}
    lands = sorted(share)
    wsum = sum(share.values())
    adj_tot = sum(st["adj"].values()) or 1
    padj = {}
    for a in lands:
        for b in lands:
            key = f"{min(a, b)}|{max(a, b)}"
            padj[(a, b)] = (st["adj"].get(key, 0) + 0.5) / adj_tot

    knn = []
    for i in range(n):
        d = sorted(range(n), key=lambda j: (seeds[i][0] - seeds[j][0]) ** 2
                   + (seeds[i][1] - seeds[j][1]) ** 2)
        knn.append([j for j in d[1:4]])

    def draw():
        r = rng.random() * wsum
        acc = 0
        for t in lands:
            acc += share[t]
            if r <= acc:
                return t
        return lands[-1]

    terrs = [draw() for _ in range(n)]

    def node_e(i, t):
        return -sum(math.log(padj[(t, terrs[j])]) for j in knn[i])

    for _ in range(iters):
        i = rng.randrange(n)
        t_new = draw()
        if t_new == terrs[i]:
            continue
        dE = node_e(i, t_new) - node_e(i, terrs[i])
        if dE <= 0 or rng.random() < math.exp(-dE):
            terrs[i] = t_new
    return terrs


# ---------------------------------------------------------------------------
# 3. capacity-constrained growth
# ---------------------------------------------------------------------------

def _grow(W, H, land, seeds, caps, rng):
    """Multi-source Dijkstra with jittered costs; a zone stops claiming at its capacity.
    Leftover pockets (all reachable zones full) are attached to the nearest assigned zone."""
    noise = ZE._value_noise(W, H, 5, rng)
    cost = [[1.0 + JITTER * (noise[y][x] + 1.0) / 2.0 for x in range(W)] for y in range(H)]
    label = [[-1] * W for _ in range(H)]
    count = [0] * len(seeds)
    heap = []
    cnt = 0
    for i, (sx, sy) in enumerate(seeds):
        heapq.heappush(heap, (0.0, cnt, sx, sy, i)); cnt += 1
    while heap:
        d, _, x, y, i = heapq.heappop(heap)
        if label[y][x] != -1 or not land[y][x] or count[i] >= caps[i]:
            continue
        label[y][x] = i
        count[i] += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx2, ny2 = x + dx, y + dy
            if 0 <= nx2 < W and 0 <= ny2 < H and land[ny2][nx2] and label[ny2][nx2] == -1:
                heapq.heappush(heap, (d + cost[ny2][nx2], cnt, nx2, ny2, i)); cnt += 1
    # leftovers: BFS attach to nearest labelled neighbour (capacity soft here)
    q = collections.deque((x, y) for y in range(H) for x in range(W)
                          if land[y][x] and label[y][x] != -1)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx2, ny2 = x + dx, y + dy
            if 0 <= nx2 < W and 0 <= ny2 < H and land[ny2][nx2] and label[ny2][nx2] == -1:
                label[ny2][nx2] = label[y][x]
                q.append((nx2, ny2))
    return label


# ---------------------------------------------------------------------------
# 4. boundary texturing (the Markov chain, clamped to the border band)
# ---------------------------------------------------------------------------

def _texture_boundaries(grid, rng, sweeps=3):
    """Isotropic Gibbs sweeps of the learned 4-neighbour terrain conditional, RESTRICTED to
    tiles within BAND (Chebyshev) of a terrain change; everything else is clamped, so the
    interiors keep their planned terrain and only the borders gain corpus transition texture."""
    import markov_terrain as MT
    H = len(grid); W = len(grid[0])
    M4 = MT.learn4(0)
    M = MT.learn(0)
    band = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            t = grid[y][x]
            if any(0 <= x + dx < W and 0 <= y + dy < H and grid[y + dy][x + dx] != t
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                for dy in range(-BAND, BAND + 1):
                    for dx in range(-BAND, BAND + 1):
                        if 0 <= x + dx < W and 0 <= y + dy < H:
                            band[y + dy][x + dx] = True
    tiles = [(x, y) for y in range(1, H - 1) for x in range(1, W - 1) if band[y][x]]
    for _ in range(sweeps):
        rng.shuffle(tiles)
        for (x, y) in tiles:
            l, u = grid[y][x - 1], grid[y - 1][x]
            r, d = grid[y][x + 1], grid[y + 1][x]
            if sum(M4["full"][(l, u, r, d)].values()) >= 10:
                dist = M4["full"][(l, u, r, d)]
            else:
                dist = collections.Counter()
                dist.update(M4["horiz"][(l, r)])
                dist.update(M4["vert"][(u, d)])
                if not dist:
                    dist = M["marg"]
            grid[y][x] = MT._sample(dist, rng)
    return grid


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def generate(W, H, seed=3, water=None, texture=True, water_mode="normal"):
    """Macro terrain grid (H rows x W cols of terrain ids). `water` overrides the corpus-drawn
    water fraction; `water_mode` picks the water STYLE: 'none' (pure land), 'normal'
    (corpus-drawn seas/lakes), 'islands' (dominant water + finer noise -> archipelago).
    Deterministic in `seed`."""
    rng = random.Random(seed)
    st = mine_macro()
    if water_mode == "none":
        wf, cell = 0.0, None
    elif water_mode == "islands":
        wf = rng.uniform(0.45, 0.60) if water is None else water
        cell = max(4, min(W, H) // 10)
    else:
        wf = rng.choice(st["water_fracs"]) if water is None else water
        cell = None
    wmask = _water_mask(W, H, wf, rng, cell)
    land = [[not wmask[y][x] for x in range(W)] for y in range(H)]
    budget = sum(1 for row in land for v in row if v)

    caps = _sample_areas(st, budget, rng)
    n = len(caps)
    land_tiles = [(x, y) for y in range(H) for x in range(W) if land[y][x]]
    seeds = []
    tries = 0
    mind2 = (0.7 * (budget / max(n, 1)) ** 0.5) ** 2
    while len(seeds) < n and tries < n * 200:
        p = land_tiles[rng.randrange(len(land_tiles))]
        tries += 1
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= mind2 for q in seeds):
            seeds.append(p)
    while len(seeds) < n:
        seeds.append(land_tiles[rng.randrange(len(land_tiles))])
    caps.sort(reverse=True)                          # biggest zones get the best-spread seeds
    terrs = _assign_terrains(seeds, st, rng)

    label = _grow(W, H, land, seeds, caps, rng)
    grid = [[WATER if not land[y][x] else terrs[label[y][x]] if label[y][x] >= 0 else terrs[0]
             for x in range(W)] for y in range(H)]
    if texture:
        _texture_boundaries(grid, rng)
    return grid


def report(grid):
    """Acceptance metrics of §4.3: zone count + share of land area in zones >= 60 tiles."""
    lvl = [[{"t": t, "river": False, "road": False} for t in row] for row in grid]
    zones, zl, _ = ZE._segment_level(lvl)
    land_area = sum(z["area"] for z in zones.values() if 0 <= z["terrain_type"] < 8)
    big = [z for z in zones.values() if z["area"] >= 60 and 0 <= z["terrain_type"] < 8]
    share = sum(z["area"] for z in big) / max(land_area, 1)
    return {"zones": len(zones), "big_zones": len(big), "big_share": round(share, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--size", type=int, default=72)
    ap.add_argument("--water", type=float, default=None)
    ap.add_argument("--regen-stats", action="store_true")
    args = ap.parse_args()
    if args.regen_stats:
        mine_macro(force=True)
    st = mine_macro()
    print(f"macro stats: {len(st['areas'])} corpus zones, "
          f"median area {st['areas'][len(st['areas']) // 2]}, "
          f"median water frac {st['water_fracs'][len(st['water_fracs']) // 2]:.2f}")
    grid = generate(args.size, args.size, seed=args.seed, water=args.water)
    print("generated:", report(grid))
    import render as RND
    lvl = [[{"t": t, "river": False, "road": False} for t in row] for row in grid]
    img = RND.render_level(lvl, [], args.size, args.size)
    out = os.path.join(ROOT, "out", "render", "pp", f"macro_s{args.seed}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print("->", out)


if __name__ == "__main__":
    main()
