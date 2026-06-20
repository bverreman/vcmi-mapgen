"""Zone-graph map generator — the missing MAP-LEVEL layer (VCMI-RMG-style).

Every prior generator imitates *local texture*; this one plans the *global graph* first and lets
the existing engine fill it. Three stages:

  1. **mine**    — aggregate the corpus into graph statistics (zone count, terrain/role mix, zone
                   degree distribution, town count) over `maps_json/`.
  2. **sample**  — draw a connected zone graph: nodes get a terrain + role, a spanning tree
                   guarantees reachability, extra edges match the corpus degree, towns are spread
                   out and value/role tiers rise OUTWARD from towns (the gradient nothing modelled).
  3. **realize** — paint a warped-Voronoi terrain from the node seeds and **wall only the
                   non-edges with rock**, so planned-adjacent zones keep a land border (a passage
                   forms there) while the rest become mountain seams. The result feeds straight
                   into `generate_map` (roles injected via `role_seeds`), reusing the entire
                   zone-assembly back half.

Connectivity-first by construction: the open spanning tree is all land, so a hero can always walk
the map — the failure mode that sinks region/markov/jigsaw on the yardstick. Torch-free, seeded.
``zone_engine`` imports this lazily for ``--layout graph``; this imports ``zone_engine``/``mapeval``
lazily — no import cycle.
"""
from __future__ import annotations

import collections
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATER, ROCK = 8, 9
ROLES = ["town", "mine", "dwelling", "treasure", "passage"]
_CACHE_PATH = os.path.join(ROOT, "out", "graph_stats.json")
_STATS = None


# ---------------------------------------------------------------------------
# 1. Mine the corpus into graph statistics (cached)
# ---------------------------------------------------------------------------

def _zone_role(objs):
    """Coarse role of a corpus zone from the purposes of its objects (town > mine > dwelling >
    treasure > passage)."""
    import mapeval as ME
    purposes = {ME._purpose(o) for o in objs}
    if "TOWN" in purposes:
        return "town"
    if "MINE" in purposes:
        return "mine"
    if "DWELLING" in purposes:
        return "dwelling"
    if purposes & ME.REWARD_PURPOSES:
        return "treasure"
    return "passage"


def mine_corpus():
    """Aggregate graph stats over the corpus; cached in memory + ``out/graph_stats.json``."""
    global _STATS
    if _STATS is not None:
        return _STATS
    if os.path.exists(_CACHE_PATH):
        _STATS = json.load(open(_CACHE_PATH))
        _STATS["terr_freq"] = {int(k): v for k, v in _STATS["terr_freq"].items()}
        return _STATS
    import obj_resolve as OR
    import mapeval as ME
    node_counts, town_counts, degrees = [], [], []
    terr_freq = collections.Counter()
    role_freq = collections.Counter()
    for nm in OR.all_map_names():
        try:
            fm = OR.load_faithful(nm)
            terr = fm["terrain"][0]
            H = len(terr); W = len(terr[0]) if H else 0
            zones, zone_label = ME._segment(terr)
            adj, big = ME._adjacency(zones, zone_label, W, H)
        except Exception:
            continue
        big = sorted(big)
        if not big:
            continue
        # bucket this map's surface objects by zone for role inference
        byz = collections.defaultdict(list)
        for o in fm["objects"]:
            if o.get("l", 0) != 0:
                continue
            x, y = o["x"], o["y"]
            if 0 <= x < W and 0 <= y < H and zone_label[y][x] in zones:
                byz[zone_label[y][x]].append(o)
        node_counts.append(len(big))
        ntowns = 0
        for zid in big:
            r = _zone_role(byz.get(zid, []))
            role_freq[r] += 1
            terr_freq[zones[zid]["terrain_type"]] += 1
            degrees.append(len(adj.get(zid, ())))
            if r == "town":
                ntowns += 1
        town_counts.append(ntowns)
    _STATS = {"node_counts": node_counts, "town_counts": town_counts, "degrees": degrees,
              "terr_freq": dict(terr_freq), "role_freq": dict(role_freq)}
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    json.dump(_STATS, open(_CACHE_PATH, "w"))
    return _STATS


# ---------------------------------------------------------------------------
# weighted sampling helpers
# ---------------------------------------------------------------------------

def _wchoice(freq, rng, exclude=()):
    items = [(k, w) for k, w in freq.items() if k not in exclude and w > 0]
    tot = sum(w for _, w in items)
    if not items:
        return None
    r = rng.random() * tot
    acc = 0
    for k, w in items:
        acc += w
        if r <= acc:
            return k
    return items[-1][0]


def _sample_land_terrain(st, rng):
    t = _wchoice(st["terr_freq"], rng, exclude=())
    while t in (WATER, ROCK) or t is None:
        t = _wchoice(st["terr_freq"], rng, exclude=(WATER, ROCK))
        if t is None:
            return 2          # grass fallback
    return t


# ---------------------------------------------------------------------------
# graph construction over the realized Voronoi adjacency
# ---------------------------------------------------------------------------

def _spanning_tree(adj, seeds):
    """Prim's MST over the realized adjacency, edge cost = seed Euclidean distance. Guarantees a
    connected open backbone (so the map is always walkable)."""
    nodes = list(adj)
    if not nodes:
        return set()

    def d(a, b):
        return (seeds[a][0] - seeds[b][0]) ** 2 + (seeds[a][1] - seeds[b][1]) ** 2
    start = nodes[0]
    seen = {start}
    tree = set()
    while len(seen) < len(nodes):
        best = None
        for u in seen:
            for v in adj[u]:
                if v not in seen and (best is None or d(u, v) < best[0]):
                    best = (d(u, v), u, v)
        if best is None:
            break                          # adjacency disconnected (shouldn't happen for a partition)
        _c, u, v = best
        seen.add(v)
        tree.add(tuple(sorted((u, v))))
    return tree


def _add_extra_edges(adj, tree, target_deg, n, rng):
    """Open extra realized-adjacency edges until the mean open degree ~ the corpus target."""
    all_edges = {tuple(sorted((a, b))) for a in adj for b in adj[a]}
    extra = sorted(all_edges - tree)
    rng.shuffle(extra)
    want = max(0, round(target_deg * n / 2) - len(tree))
    return set(extra[:want])


def _farthest_points(seeds, k):
    """k seed indices chosen greedily for maximum mutual spread (balanced town placement)."""
    n = len(seeds)
    if k >= n:
        return set(range(n))
    chosen = [max(range(n), key=lambda i: seeds[i][0] + seeds[i][1])]   # deterministic start
    while len(chosen) < k:
        nxt = max((i for i in range(n) if i not in chosen),
                  key=lambda i: min((seeds[i][0] - seeds[j][0]) ** 2
                                    + (seeds[i][1] - seeds[j][1]) ** 2 for j in chosen))
        chosen.append(nxt)
    return set(chosen)


def _bfs_tiers(open_edges, towns, n):
    """Graph distance of every node from the nearest town over the OPEN edges."""
    g = collections.defaultdict(set)
    for a, b in open_edges:
        g[a].add(b); g[b].add(a)
    tier = {i: math.inf for i in range(n)}
    q = collections.deque()
    for t in towns:
        tier[t] = 0; q.append(t)
    while q:
        u = q.popleft()
        for v in g[u]:
            if tier[v] > tier[u] + 1:
                tier[v] = tier[u] + 1
                q.append(v)
    return tier


def _colour_terrains(adj, n, st, rng):
    """Assign each node a corpus-weighted LAND terrain that differs from its already-coloured
    neighbours (greedy). Keeps adjacent zones distinct so each stays its own walled zone; falls
    back to any land terrain if a node's neighbours already use every option."""
    terrs = {}
    for i in range(n):
        used = {terrs[j] for j in adj.get(i, ()) if j in terrs}
        t = None
        for _ in range(12):
            cand = _sample_land_terrain(st, rng)
            if cand not in used:
                t = cand
                break
        if t is None:
            opts = [c for c in range(8) if c not in used] or [2]
            t = rng.choice(opts)
        terrs[i] = t
    return [terrs[i] for i in range(n)]


def _assign_roles(st, n, towns, tier, rng):
    """town for town nodes; outer nodes (far from a town) become treasure → an OUTWARD value
    gradient; inner nodes sample mine/dwelling/passage from the corpus role mix."""
    finite = [tier[i] for i in range(n) if i not in towns and tier[i] != math.inf]
    med = sorted(finite)[len(finite) // 2] if finite else 0
    roles = {}
    for i in range(n):
        if i in towns:
            roles[i] = "town"
        elif tier[i] != math.inf and tier[i] > med:
            roles[i] = "treasure"
        else:
            roles[i] = _wchoice(st["role_freq"], rng, exclude=("town", "treasure")) or "passage"
    return roles


# ---------------------------------------------------------------------------
# 2 + 3.  sample graph  →  realize terrain
# ---------------------------------------------------------------------------

def realize(seed, size):
    """Plan a zone graph and paint it as PURE-LAND terrain (no water, no subterranean rock).
    Zones are separated by the grammar's mountain/tree RIM WALL — passages are carved only at
    planned graph edges, so every other border stays a solid vegetation belt and each zone keeps
    at least one entrance (the spanning tree guarantees ≥1 open edge per zone). Returns
    (grid, W, H, info, plan) with plan = {role_seeds, edge_seeds}."""
    import zone_engine as ZE
    rng = random.Random(seed)
    st = mine_corpus()
    W = H = size
    # zone count is bounded by map size (corpus informs mix/degree, not raw N on a fixed canvas)
    n = max(4, min(round(W * H / 440), 16))
    seeds = ZE._poisson_seeds(W, H, n, rng)

    # warped-Voronoi partition (organic borders), reusing the region-layout noise
    nx, ny = ZE._value_noise(W, H, 9, rng), ZE._value_noise(W, H, 9, rng)
    amp = size / 7.0
    label = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            wx, wy = x + amp * nx[y][x], y + amp * ny[y][x]
            label[y][x] = min(range(n), key=lambda k:
                              (wx - seeds[k][0]) ** 2 + (wy - seeds[k][1]) ** 2)

    # realized node adjacency (4-neighbour label contact)
    adj = collections.defaultdict(set)
    for y in range(H):
        for x in range(W):
            a = label[y][x]
            for dx, dy in ((1, 0), (0, 1)):
                bx, by = x + dx, y + dy
                if bx < W and by < H:
                    b = label[by][bx]
                    if a != b:
                        adj[a].add(b); adj[b].add(a)

    # distinct terrain on ADJACENT nodes (greedy graph colouring) so segmentation keeps each
    # planned node as its OWN zone — otherwise same-terrain neighbours merge and lose their wall.
    terrs = _colour_terrains(adj, n, st, rng)

    # connected backbone + corpus-degree extras = the OPEN edges (the planned passages)
    tree = _spanning_tree(adj, seeds)
    deg = st["degrees"]
    target_deg = min(max(1, round(sum(deg) / len(deg))) if deg else 2, n - 1)
    open_edges = tree | _add_extra_edges(adj, tree, target_deg, n, rng)

    # towns spread out; roles tier OUTWARD from towns
    n_towns = min(max(2, round(sum(st["town_counts"]) / max(len(st["town_counts"]), 1))),
                  max(2, n // 3))
    towns = _farthest_points(seeds, n_towns)
    tier = _bfs_tiers(open_edges, towns, n)
    roles = _assign_roles(st, n, towns, tier, rng)

    # PURE-LAND paint — no water, no rock. The grammar lays a mountain/tree rim wall on every
    # zone edge; passages are carved (downstream) ONLY at the open edges, so non-edge borders stay
    # a solid vegetation belt and each zone has ≥1 entrance.
    terr_id = [[terrs[label[y][x]] for x in range(W)] for y in range(H)]
    grid = ZE.tile_terrain(terr_id, W, H)

    role_seeds = [(seeds[i][0], seeds[i][1], roles[i]) for i in range(n)]
    edge_seeds = [(seeds[a], seeds[b]) for (a, b) in open_edges]
    plan = {"role_seeds": role_seeds, "edge_seeds": edge_seeds}
    info = f"graph(N={n},towns={len(towns)},open={len(open_edges)},deg~{target_deg})"
    return grid, W, H, info, plan


if __name__ == "__main__":   # quick stats dump
    s = mine_corpus()
    print("corpus graph stats:",
          {k: (v if not isinstance(v, list) else f"n={len(v)} mean={sum(v)/max(len(v),1):.1f}")
           for k, v in s.items()})
