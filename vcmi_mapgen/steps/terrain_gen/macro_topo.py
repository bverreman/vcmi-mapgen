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

    uv run python -m vcmi_mapgen.steps.terrain_gen.macro_topo --seed 3 --size 72
"""
import argparse
import collections
import heapq
import json
import math
import os
import random

from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen.vcmi_paths import project_root

ROOT = project_root()
STATS_PATH = str(ROOT / "data" / "pp" / "macro_stats.json")
STATS_PATH_UNDERGROUND = str(ROOT / "data" / "pp" / "macro_stats_underground.json")
WATER, ROCK = 8, 9
MIN_ZONE_AREA = 40              # floor for sampled target areas
JITTER = 1.4                    # growth-cost noise amplitude (0 = pure Voronoi-like fronts)
BAND = 2                        # boundary-texturing band half-width (tiles)


# ---------------------------------------------------------------------------
# 1. corpus macro statistics
# ---------------------------------------------------------------------------

def mine_macro(level=0, force=False):
    """Corpus macro stats for terrain level `level` (0 = surface, 1 = underground). The
    underground table is mined independently from `fm["terrain"][1]` of two-level corpus
    maps — real underground zone areas/adjacency/barrier fraction are statistically distinct
    from the surface (rock, not subterr, is the dominant barrier terrain there; see corpus
    histograms in the design notes), so it is never derived from or blended with level-0 stats."""
    from vcmi_mapgen import obj_resolve as OR
    path = STATS_PATH if level == 0 else STATS_PATH_UNDERGROUND
    if not force and os.path.exists(path):
        return json.load(open(path))
    barrier = WATER if level == 0 else ROCK
    areas, barrier_fracs = [], []
    terr_share = collections.Counter()
    adj = collections.Counter()                      # "t1|t2" boundary-tile counts, t1 <= t2
    nzones = []
    for nm in OR.all_map_names():
        try:
            fm = OR.load_faithful(nm)
        except Exception:
            continue
        if level >= len(fm["terrain"]):
            continue
        lvl = fm["terrain"][level]
        H = len(lvl); W = len(lvl[0]) if H else 0
        T = [[c["t"] for c in row] for row in lvl]
        nb = sum(1 for row in T for t in row if t == barrier)
        barrier_fracs.append(nb / max(W * H, 1))
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
    st = {"areas": sorted(areas), "barrier_fracs": sorted(barrier_fracs),
          "terr_share": {str(k): v for k, v in terr_share.items()},
          "adj": dict(adj), "nzones": nzones}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(st, open(path, "w"))
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


def _carve_corridor(land, a, b, W, H, rng, half_w=1, protect=None):
    """Drunken walk from `a` toward `b`, marking a `half_w`-radius band as land — a tunnel,
    not a straight line, so it reads as a cave passage rather than a ruler-drawn corridor.
    Cells are also added to `protect` (if given): a thin corridor sits entirely inside the
    boundary-texturing band on both sides, so without protection `_texture_boundaries`'s
    Gibbs resampling — drawing from a rock-heavy corpus conditional — can erode the whole
    tunnel back to rock, disconnecting caverns `_tunnel_mask` had genuinely joined."""
    x, y = float(a[0]), float(a[1])
    bx, by = b
    for _ in range(8 * (abs(a[0] - bx) + abs(a[1] - by)) + 40):
        ix, iy = int(round(x)), int(round(y))
        for dy in range(-half_w, half_w + 1):
            for dx in range(-half_w, half_w + 1):
                nx, ny = ix + dx, iy + dy
                if 0 <= nx < W and 0 <= ny < H:
                    land[ny][nx] = True
                    if protect is not None:
                        protect.add((nx, ny))
        if (ix, iy) == (bx, by):
            break
        ddx, ddy = bx - x, by - y
        dist = max((ddx ** 2 + ddy ** 2) ** 0.5, 1e-6)
        x += ddx / dist * 0.8 + rng.uniform(-0.6, 0.6)
        y += ddy / dist * 0.8 + rng.uniform(-0.6, 0.6)
        x = min(max(x, 1.0), W - 2.0)
        y = min(max(y, 1.0), H - 2.0)


def _tunnel_mask(W, H, land_frac, rng):
    """Connected cavern+tunnel land mask for the underground level: a handful of organic
    cavern blobs joined by random-walk corridors (a minimum-spanning-tree over cavern
    centers, so every cavern is reachable on foot), rather than reusing the surface's
    water-mask (noise thresholded at a quantile) which produces a scattered archipelago —
    correct for open water, wrong for rock: rock is a WALL, not something a hero swims
    across, so an underground level built the same way as a sea strands every cavern in
    its own sealed pocket. This shape instead matches the user's description of real H3
    undergrounds: tunnels leading to larger patches, not islands."""
    budget = max(1, int(round(land_frac * W * H)))
    n_caverns = max(3, min(8, budget // 90))
    margin = 6
    centers = []
    tries = 0
    mind2 = (0.6 * (W * H / n_caverns) ** 0.5) ** 2
    while len(centers) < n_caverns and tries < n_caverns * 300:
        p = (rng.randint(margin, W - margin - 1), rng.randint(margin, H - margin - 1))
        tries += 1
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= mind2 for q in centers):
            centers.append(p)
    while len(centers) < n_caverns:
        centers.append((rng.randint(margin, W - margin - 1), rng.randint(margin, H - margin - 1)))

    noise = ZE._value_noise(W, H, 5, rng)
    land = [[False] * W for _ in range(H)]
    protect = set()
    avg_r = max(3.0, (budget / max(n_caverns, 1) / math.pi) ** 0.5 * 0.7)
    for cx, cy in centers:
        r = avg_r * rng.uniform(0.7, 1.4)
        for y in range(max(0, cy - int(r) - 2), min(H, cy + int(r) + 3)):
            for x in range(max(0, cx - int(r) - 2), min(W, cx + int(r) + 3)):
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                wobble = r * (0.75 + 0.35 * noise[y][x])
                if d <= wobble:
                    land[y][x] = True

    # MST over cavern centers (nearest-unconnected-first) + a few extra loop edges
    connected = {0}
    remaining = set(range(1, len(centers)))
    edges = []
    while remaining:
        best = None
        for i in connected:
            for j in remaining:
                dx = centers[i][0] - centers[j][0]
                dy = centers[i][1] - centers[j][1]
                d2 = dx * dx + dy * dy
                if best is None or d2 < best[0]:
                    best = (d2, i, j)
        _, i, j = best
        edges.append((i, j))
        connected.add(j)
        remaining.discard(j)
    for _ in range(max(0, n_caverns // 4)):
        i, j = rng.sample(range(len(centers)), 2)
        if (i, j) not in edges and (j, i) not in edges:
            edges.append((i, j))
    for i, j in edges:
        _carve_corridor(land, centers[i], centers[j], W, H, rng, protect=protect)
    return land, protect


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

def _texture_boundaries(grid, rng, sweeps=3, level=0, protect=frozenset()):
    """Isotropic Gibbs sweeps of the learned 4-neighbour terrain conditional, RESTRICTED to
    tiles within BAND (Chebyshev) of a terrain change; everything else is clamped, so the
    interiors keep their planned terrain and only the borders gain corpus transition texture.
    `level` selects which terrain level's corpus transitions to learn from (0 or 1);
    `markov_terrain.learn`/`learn4` already filter to maps that have that level. `protect`
    cells (e.g. underground tunnel corridors, which are thin enough to sit entirely inside
    the band on both sides) are excluded from resampling so a rock-heavy corpus conditional
    can't erode a load-bearing connection back into barrier."""
    from vcmi_mapgen import markov_terrain as MT
    H = len(grid); W = len(grid[0])
    M4 = MT.learn4(level)
    M = MT.learn(level)
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
    tiles = [(x, y) for y in range(1, H - 1) for x in range(1, W - 1)
             if band[y][x] and (x, y) not in protect]
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

def generate(W, H, seed=3, water=None, texture=True, water_mode="normal", level=0,
             protect_out=None):
    """Macro terrain grid (H rows x W cols of terrain ids) for terrain level `level` (0 =
    surface, 1 = underground). `water` overrides the corpus-drawn barrier fraction (water on
    the surface, rock underground); `water_mode` picks the surface water STYLE: 'none' (pure
    land), 'normal' (corpus-drawn seas/lakes), 'islands' (dominant water + finer noise ->
    archipelago) — the underground level always carves its barrier (rock) at a corpus-drawn
    fraction, but as a connected cavern+tunnel network (`_tunnel_mask`), not a water-style
    archipelago: rock is a wall a hero cannot swim past, so undergrounds must stay walkable
    between caverns the way real H3 maps do (tunnels leading to larger patches).
    `protect_out`, if given a set, is updated in-place with the tunnel-corridor cells that
    downstream steps (notably `zone_engine.tile_terrain`'s despeckle merge) must never
    reassign to a barrier code, or a thin corridor can be eroded back into rock after
    `generate()` already built it connected.
    Deterministic in `seed`."""
    rng = random.Random(seed)
    st = mine_macro(level=level)
    barrier = WATER if level == 0 else ROCK
    protect = frozenset()
    if level == 1:
        rf = rng.choice(st["barrier_fracs"]) if water is None else water
        land, protect = _tunnel_mask(W, H, 1.0 - rf, rng)
    else:
        if water_mode == "none":
            wf, cell = 0.0, None
        elif water_mode == "islands":
            wf = rng.uniform(0.45, 0.60) if water is None else water
            cell = max(4, min(W, H) // 10)
        else:
            wf = rng.choice(st["barrier_fracs"]) if water is None else water
            cell = None
        bmask = _water_mask(W, H, wf, rng, cell)
        land = [[not bmask[y][x] for x in range(W)] for y in range(H)]
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
    grid = [[barrier if not land[y][x] else terrs[label[y][x]] if label[y][x] >= 0 else terrs[0]
             for x in range(W)] for y in range(H)]
    if texture:
        _texture_boundaries(grid, rng, level=level, protect=protect)
    if protect_out is not None:
        protect_out |= protect
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
    ap.add_argument("--level", type=int, default=0, help="0=surface, 1=underground")
    ap.add_argument("--regen-stats", action="store_true")
    args = ap.parse_args()
    if args.regen_stats:
        mine_macro(level=args.level, force=True)
    st = mine_macro(level=args.level)
    barrier_name = "water" if args.level == 0 else "rock"
    print(f"macro stats (level {args.level}): {len(st['areas'])} corpus zones, "
          f"median area {st['areas'][len(st['areas']) // 2]}, "
          f"median {barrier_name} frac {st['barrier_fracs'][len(st['barrier_fracs']) // 2]:.2f}")
    grid = generate(args.size, args.size, seed=args.seed, water=args.water, level=args.level)
    print("generated:", report(grid))
    from PIL import Image
    from vcmi_mapgen.vcmi_ids import TERRAIN_RGB, TERRAIN_TILE_PX as _TILE
    lvl = [[{"t": t, "river": False, "road": False} for t in row] for row in grid]
    img = Image.new("RGB", (args.size * _TILE, args.size * _TILE))
    px = img.load()
    for y, row in enumerate(lvl):
        for x, cell in enumerate(row):
            r, g, b = TERRAIN_RGB.get(cell["t"], (0, 0, 0))
            for dy in range(_TILE):
                for dx in range(_TILE):
                    px[x * _TILE + dx, y * _TILE + dy] = (r, g, b)
    out = str(ROOT / "out" / "render" / "pp" / f"macro_s{args.seed}.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out)
    print("->", out)


if __name__ == "__main__":
    main()
