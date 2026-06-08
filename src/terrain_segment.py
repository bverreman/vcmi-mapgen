"""Terrain-type flood-fill segmentation and per-tile static feature extraction.

Segments a terrain level (2-D grid of {"t": int, ...} cells) into contiguous
natural zones — one zone per connected region of the same terrain code,
excluding water (8) and rock (9) which act as barriers.

Public API
----------
segment(terrain_level)
    -> (zones: dict[int, zone_dict], zone_label: H×W list[list[int]])

compute_static_features(terrain_level, zones, zone_label)
    -> numpy array shape (H, W, DIM_STATIC=28)

DIM_STATIC = 28  (exported constant)
"""
from __future__ import annotations

import collections
import math
import sys
import os

import numpy as np

WATER, ROCK = 8, 9
DIM_STATIC = 32  # feature vector length (see compute_static_features docstring)


# ---------------------------------------------------------------------------
# BFS helpers
# ---------------------------------------------------------------------------

def _bfs_within_zone(tiles_set: set, sources: set) -> dict:
    """BFS confined to tiles_set from multiple sources. Returns {(x,y): dist}."""
    dist: dict = {}
    dq: collections.deque = collections.deque()
    for t in sources:
        if t in tiles_set and t not in dist:
            dist[t] = 0
            dq.append(t)
    while dq:
        x, y = dq.popleft()
        d = dist[(x, y)]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if nb in tiles_set and nb not in dist:
                dist[nb] = d + 1
                dq.append(nb)
    return dist


def _bfs_global(H: int, W: int, sources: set, walls: set) -> np.ndarray:
    """Multi-source BFS on H×W grid, returning float32 distance array (INF for unreachable)."""
    INF = float(H * W + 1)
    dist = np.full((H, W), INF, dtype=np.float32)
    dq: collections.deque = collections.deque()
    for x, y in sources:
        if 0 <= x < W and 0 <= y < H and (x, y) not in walls and dist[y, x] == INF:
            dist[y, x] = 0.0
            dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        nd = dist[y, x] + 1.0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and dist[ny, nx] == INF and (nx, ny) not in walls:
                dist[ny, nx] = nd
                dq.append((nx, ny))
    return dist


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

# Subdivision defaults: a same-terrain region larger than MAX_ZONE_AREA holds
# several design roles at once (a town area + treasure pockets + buffer), so we
# split it into compact sub-zones of ~TARGET_SUBZONE_AREA tiles, each of which
# carries a single archetype. (spec Open Question #5.)
MAX_ZONE_AREA = 300
TARGET_SUBZONE_AREA = 200


def _flood_fill(terrain_level: list) -> list:
    """4-connected flood-fill by terrain type. Returns zone_label (H×W, -1=barrier)."""
    H = len(terrain_level)
    W = len(terrain_level[0])
    zone_label: list = [[-1] * W for _ in range(H)]
    zone_id = 0
    for y0 in range(H):
        for x0 in range(W):
            t0 = terrain_level[y0][x0]["t"]
            if t0 in (WATER, ROCK) or zone_label[y0][x0] != -1:
                continue
            zone_label[y0][x0] = zone_id
            dq: collections.deque = collections.deque([(x0, y0)])
            while dq:
                x, y = dq.popleft()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if (0 <= nx < W and 0 <= ny < H
                            and zone_label[ny][nx] == -1
                            and terrain_level[ny][nx]["t"] == t0):
                        zone_label[ny][nx] = zone_id
                        dq.append((nx, ny))
            zone_id += 1
    return zone_label


def _kmeans_positions(tiles: list, k: int, iters: int = 40) -> list:
    """Partition tile coords into k spatially-compact clusters (k-means++, seed 0).
    Returns a list of cluster indices aligned with `tiles`."""
    pts = np.asarray(tiles, dtype=np.float64)
    n = len(pts)
    if k <= 1 or n <= k:
        return [0] * n if k <= 1 else list(range(n))
    rng = np.random.RandomState(0)
    # k-means++ seeding
    centers = [pts[rng.randint(n)]]
    for _ in range(k - 1):
        d2 = np.min(np.stack([np.sum((pts - c) ** 2, axis=1) for c in centers]), axis=0)
        s = d2.sum()
        probs = d2 / s if s > 0 else np.ones(n) / n
        centers.append(pts[rng.choice(n, p=probs)])
    centers = np.array(centers, dtype=np.float64)
    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        dists = np.linalg.norm(pts[:, None, :] - centers[None, :, :], axis=2)
        new = dists.argmin(axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            mask = labels == j
            if mask.any():
                centers[j] = pts[mask].mean(axis=0)
    return labels.tolist()


def _subdivide_label(zone_label: list, max_area: int, target_area: int) -> list:
    """Re-label so any zone with area > max_area is split into ~area/target_area
    compact sub-zones (k-means on tile positions). Returns a new H×W label."""
    H = len(zone_label)
    W = len(zone_label[0])
    tiles_by_zone: dict = collections.defaultdict(list)
    for y in range(H):
        for x in range(W):
            z = zone_label[y][x]
            if z != -1:
                tiles_by_zone[z].append((x, y))

    new_label: list = [[-1] * W for _ in range(H)]
    next_id = 0
    for _z, tiles in sorted(tiles_by_zone.items()):
        area = len(tiles)
        if area <= max_area:
            for x, y in tiles:
                new_label[y][x] = next_id
            next_id += 1
        else:
            k = max(2, round(area / target_area))
            assign = _kmeans_positions(tiles, k)
            base = next_id
            used = set()
            for (x, y), a in zip(tiles, assign):
                new_label[y][x] = base + a
                used.add(a)
            next_id = base + k
    return new_label


def _compute_attrs(terrain_level: list, zone_label: list) -> dict:
    """Compute per-zone attributes from a finished zone_label.

    A "zone" here is any label != -1 (a terrain region OR a sub-zone of one).
    Boundary/adjacency are relative to the label, so sub-zone-internal borders
    count as boundaries — exactly what gives finer sub-zone features.
    """
    H = len(terrain_level)
    W = len(terrain_level[0])
    tiles_by_zone: dict = collections.defaultdict(list)
    for y in range(H):
        for x in range(W):
            z = zone_label[y][x]
            if z != -1:
                tiles_by_zone[z].append((x, y))

    zones: dict = {}
    for zid, tiles in tiles_by_zone.items():
        area = len(tiles)
        t0 = terrain_level[tiles[0][1]][tiles[0][0]]["t"]
        cx = sum(x for x, y in tiles) / area
        cy = sum(y for x, y in tiles) / area
        tiles_set = set(tiles)

        boundary: set = set()
        adj_zones: set = set()
        for x, y in tiles:
            is_bnd = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H):
                    is_bnd = True
                else:
                    nt = terrain_level[ny][nx]["t"]
                    nz = zone_label[ny][nx]
                    if nt in (WATER, ROCK):
                        is_bnd = True
                    elif nz != zid:
                        is_bnd = True
                        adj_zones.add(nz)
            if is_bnd:
                boundary.add((x, y))

        # Chokepoint heuristic: boundary tile where the 5×5 passable neighbourhood
        # is very small (≤ 10 tiles), indicating a tight terrain corridor.
        chokepoints: set = set()
        for x, y in boundary:
            passable_count = 0
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H:
                        if terrain_level[ny][nx]["t"] not in (WATER, ROCK):
                            passable_count += 1
            if passable_count <= 10:
                chokepoints.add((x, y))

        zones[zid] = {
            "terrain_type": t0,
            "area": area,
            "centroid": (cx, cy),
            "tiles": tiles,
            "tiles_set": tiles_set,
            "boundary_tiles": boundary,
            "chokepoints": chokepoints,
            "adjacent_zones": adj_zones,
        }
    return zones


def segment(terrain_level: list, subdivide: bool = False,
            max_area: int = MAX_ZONE_AREA,
            target_area: int = TARGET_SUBZONE_AREA) -> tuple[dict, list]:
    """Flood-fill terrain_level by terrain type into natural zones.

    Parameters
    ----------
    terrain_level : list[list[dict]]
        H×W grid of {"t": terrain_code, ...} dicts (level 0 from corpus JSON).
    subdivide : bool
        If True, split any zone larger than `max_area` into compact sub-zones of
        ~`target_area` tiles (k-means on positions), so each sub-zone carries a
        single design role / archetype.

    Returns
    -------
    zones : dict[int, dict]
        zone_id -> {terrain_type, area, centroid, tiles, tiles_set,
                     boundary_tiles, chokepoints, adjacent_zones}
    zone_label : list[list[int]]
        H×W; -1 for water/rock, zone_id (or sub-zone id) otherwise.
    """
    zone_label = _flood_fill(terrain_level)
    if subdivide:
        zone_label = _subdivide_label(zone_label, max_area, target_area)
    zones = _compute_attrs(terrain_level, zone_label)
    return zones, zone_label


# ---------------------------------------------------------------------------
# Static feature extraction
# ---------------------------------------------------------------------------

def compute_static_features(terrain_level: list, zones: dict, zone_label: list) -> np.ndarray:
    """Compute the 39-dim static feature vector for every tile.

    Feature layout (all floats):
      [0:10]  terrain_type one-hot (10 terrain codes)
      [10:20] zone_terrain_type one-hot
      [20]    dist_boundary  — BFS within zone to nearest boundary tile, / sqrt(area)
      [21]    dist_centroid  — Euclidean to zone centroid / diagonal
      [22]    dist_water     — BFS to nearest water tile / diagonal  (capped at 1)
      [23]    dist_edge      — Manhattan to nearest map edge / diagonal
      [24]    is_chokepoint  — 1 if tile is on/near a detected chokepoint
      [25]    local_openness — fraction of tiles within radius-3 circle that are passable
      [26]    zone_area_log  — log(area+1) / log(max_area+1)
      [27]    n_adj_zones    — |adjacent_zones| / max_adj (normalised)
      [28]    x_rel          — (x - cx) / sqrt(area): zone-relative x position
      [29]    y_rel          — (y - cy) / sqrt(area): zone-relative y position
      [30]    has_road       — 1 if tile has any road
      [31]    has_river      — 1 if tile has any river

    Water/rock tiles get a zero vector (they are never candidate placement tiles).

    Returns
    -------
    feats : np.ndarray, shape (H, W, 32), dtype float32
    """
    H = len(terrain_level)
    W = len(terrain_level[0])
    diag = max(math.hypot(W, H), 1.0)

    # Terrain type integer array for vectorised ops
    terr_arr = np.array([[terrain_level[y][x]["t"] for x in range(W)]
                          for y in range(H)], dtype=np.int32)
    passable = (terr_arr != WATER) & (terr_arr != ROCK)
    walls_set = {(x, y) for y in range(H) for x in range(W) if not passable[y, x]}

    # --- dist_water: BFS from all water tiles across the whole map ---
    water_sources = {(x, y) for y in range(H) for x in range(W)
                     if terr_arr[y, x] == WATER}
    dist_water_arr = _bfs_global(H, W, water_sources, set())

    # --- per-zone BFS within zone from boundary tiles (dist_boundary) ---
    zone_bfs: dict = {}
    for zid, zone in zones.items():
        zone_bfs[zid] = _bfs_within_zone(zone["tiles_set"], zone["boundary_tiles"])

    # --- chokepoint lookup set (global) ---
    chokepoint_set: set = set()
    for zone in zones.values():
        chokepoint_set.update(zone.get("chokepoints", set()))

    # --- local openness: convolution with a radius-3 disk kernel ---
    # Build kernel
    kr = 3
    kernel = np.array([[1.0 if dx*dx + dy*dy <= kr*kr else 0.0
                         for dx in range(-kr, kr + 1)]
                        for dy in range(-kr, kr + 1)], dtype=np.float32)
    pass_f = passable.astype(np.float32)
    ones_f = np.ones((H, W), dtype=np.float32)
    pad_pass = np.pad(pass_f, kr, mode="constant", constant_values=0)
    pad_ones = np.pad(ones_f, kr, mode="constant", constant_values=0)
    kH, kW = kernel.shape
    openness_num = np.zeros((H, W), dtype=np.float32)
    openness_den = np.zeros((H, W), dtype=np.float32)
    for ky in range(kH):
        for kx in range(kW):
            if kernel[ky, kx]:
                openness_num += pad_pass[ky:ky + H, kx:kx + W]
                openness_den += pad_ones[ky:ky + H, kx:kx + W]
    openness_arr = openness_num / np.maximum(openness_den, 1.0)

    # --- normalisation constants ---
    max_area = max((z["area"] for z in zones.values()), default=1) or 1
    log_max_area = math.log(max_area + 1)
    max_adj = max((len(z["adjacent_zones"]) for z in zones.values()), default=1) or 1

    # --- assemble feature array ---
    feats = np.zeros((H, W, DIM_STATIC), dtype=np.float32)

    for y in range(H):
        for x in range(W):
            t = terr_arr[y, x]
            cell = terrain_level[y][x]

            # terrain one-hot
            if 0 <= t < 10:
                feats[y, x, t] = 1.0

            zid = zone_label[y][x]
            if zid == -1:
                continue  # water/rock: leave the rest zero

            zone = zones[zid]
            zt = zone["terrain_type"]
            if 0 <= zt < 10:
                feats[y, x, 10 + zt] = 1.0

            sq_area = math.sqrt(max(zone["area"], 1))
            ccx, ccy = zone["centroid"]

            # dist_boundary
            bfs_d = zone_bfs[zid].get((x, y), 0)
            feats[y, x, 20] = bfs_d / sq_area

            # dist_centroid
            feats[y, x, 21] = math.hypot(x - ccx, y - ccy) / diag

            # dist_water
            dw = float(dist_water_arr[y, x])
            feats[y, x, 22] = min(dw, H + W) / diag

            # dist_edge
            de = min(x, y, W - 1 - x, H - 1 - y)
            feats[y, x, 23] = de / diag

            # is_chokepoint
            feats[y, x, 24] = 1.0 if (x, y) in chokepoint_set else 0.0

            # local_openness
            feats[y, x, 25] = float(openness_arr[y, x])

            # zone_area_log
            feats[y, x, 26] = math.log(zone["area"] + 1) / log_max_area

            # n_adjacent_zones
            feats[y, x, 27] = len(zone["adjacent_zones"]) / max_adj

            # x_rel, y_rel: zone-relative position (unique per tile within zone)
            feats[y, x, 28] = (x - ccx) / sq_area
            feats[y, x, 29] = (y - ccy) / sq_area

            # road / river presence (corpus stores as bool)
            feats[y, x, 30] = 1.0 if cell.get("road") else 0.0
            feats[y, x, 31] = 1.0 if cell.get("river") else 0.0

    return feats


# ---------------------------------------------------------------------------
# Utility: chokepoint set from zones (for callers that need it)
# ---------------------------------------------------------------------------

def chokepoint_set_from_zones(zones: dict) -> set:
    """Return the union of all chokepoint tiles across all zones."""
    result: set = set()
    for zone in zones.values():
        result.update(zone.get("chokepoints", set()))
    return result
