"""Vegetation data pipeline for the learned (GAN) decoration generator — phase 1.

From the corpus this builds:
  * a compact DECORATION **category taxonomy** — one channel per semantic decoration
    `type` (mountain / oakTrees / rock / shrub / flowers / ...), the `*DUPLICATE`
    VCMI variants merged into their base, plus a trailing "OTHER" fallback. The
    decoration `type` is a much better, terrain-coupled category basis than the opaque
    `animation[:4]` prefix, and there are only ~36 of them — tractable as GAN channels.
  * an **identity pool** `{category: {terrain_id: [(identity, weight), ...]}}` so the GA
    decoder can turn a chosen category back into a concrete `{type,subtype,animation,mask}`.

and slices level-0 terrain into fixed N×N training windows:
  * CONDITION (15×N×N float32): land-terrain one-hot (8) + non-land mask (1) + depth (1)
    + sweep (1) + land mask (1) + dist-to-other-terrain (1) + map-edge proximity (1)
    + inter-zone passage (1).
  * TARGET (C×N×N float32): per-tile category occupancy — channel `c` set at the **anchor
    tile** (bottom-right per OR.mask_cells) of every DECORATION object of category `c`.

The taxonomy is cached to ``out/veg_taxonomy.json`` (gitignored). ``torch`` is imported
lazily (only :class:`WindowDataset` needs it) so non-ML callers stay torch-free.
"""
from __future__ import annotations

import collections
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR
import ontology as ON
import zone_engine as ZE  # _segment_level (segmentation + canonical depth/sweep)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY_PATH = os.path.join(ROOT, "out", "veg_taxonomy.json")

# --- window / channel geometry ---------------------------------------------
N = 32                 # window edge (tiles)
STRIDE = 16            # 50% overlap
MIN_LAND = 64          # keep a window only if it has at least this many land tiles

WATER, ROCK = 8, 9
LAND_TERRAINS = tuple(range(8))   # ids 0..7 are land; 8=water, 9=rock are barriers

# CONDITION channel layout (COND_CH total):
#   0..7  land-terrain one-hot        8 non-land (water/rock/off-map)
#   9     depth (0=rim .. 1=core)     10 sweep (angular)          11 land mask
#   12    dist-to-other-terrain (1=on a land/land seam .. 0 inland)
#   13    map-edge proximity (1=on the raster border/off-map .. 0 interior)
#   14    inter-zone passage tile (1 on the one-tile connector gap)
COND_CH = 15
_C_NONLAND, _C_DEPTH, _C_SWEEP, _C_LANDMASK = 8, 9, 10, 11
_C_DIST_OTHER, _C_EDGE, _C_PASSAGE = 12, 13, 14
DIST_OTHER_CAP = 6.0   # tiles; seam-proximity decays to 0 beyond this
EDGE_CAP = 6.0         # tiles; map-edge proximity decays to 0 beyond this


def _norm_type(t: str) -> str:
    """Merge VCMI ``*DUPLICATE`` variants into their semantic base type."""
    t = t or "OTHER"
    if t.endswith("DUPLICATE"):
        t = t[: -len("DUPLICATE")]
    return t


# ---------------------------------------------------------------------------
# Taxonomy (categories + identity pool), cached to out/veg_taxonomy.json
# ---------------------------------------------------------------------------

def build_taxonomy(force: bool = False) -> dict:
    """Scan the corpus once → {"categories": [...], "pool": {...}}; cache to disk.

    pool[category][str(terrain_id)] = [[identity_dict, weight], ...]
    """
    if not force and os.path.exists(TAXONOMY_PATH):
        with open(TAXONOMY_PATH) as fh:
            return json.load(fh)

    # category -> terrain_id -> identity_key -> [identity, count]
    acc: dict = {}
    counts: dict = {}
    for name in OR.all_map_names():
        fm = OR.load_faithful(name)
        for o in fm["objects"]:
            if OR.purpose_of(o) != "DECORATION":
                continue
            cat = _norm_type(o.get("type", "OTHER"))
            # terrain under the object's anchor
            lvl = o.get("l", 0)
            x, y = o["x"], o["y"]
            terr = _terrain_at(fm, lvl, x, y)
            ident = OR.exact_identity(o)
            key = json.dumps(ident, sort_keys=True)
            acc.setdefault(cat, {}).setdefault(str(terr), {}) \
               .setdefault(key, [ident, 0])[1] += 1
            counts[cat] = counts.get(cat, 0) + 1

    categories = sorted(counts) + ["OTHER"]
    pool = {
        cat: {tid: [v for v in by_key.values()] for tid, by_key in by_terr.items()}
        for cat, by_terr in acc.items()
    }
    pool["OTHER"] = pool.get("OTHER", {})
    out = {"categories": categories, "pool": pool}
    os.makedirs(os.path.dirname(TAXONOMY_PATH), exist_ok=True)
    with open(TAXONOMY_PATH, "w") as fh:
        json.dump(out, fh)
    return out


def _terrain_at(fm: dict, lvl: int, x: int, y: int) -> int:
    terr = fm["terrain"]
    if lvl >= len(terr):
        return WATER
    grid = terr[lvl]
    if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
        return grid[y][x]["t"]
    return WATER


# --- category vocabulary: delegated to the ONTOLOGY (single source of truth) -----------
# The decoration category vocabulary is the ontology's DECORATION type-level keys, plus a
# trailing "OTHER" channel for objects the ontology cannot categorise. Identity decoding and
# terrain coupling come from the ontology too, so the GAN/patch decoders place only
# ontology-authoritative objects — never corpus-harvested identities.

def categories() -> list[str]:
    return ON.veg_categories() + ["OTHER"]


def num_categories() -> int:
    return len(ON.veg_categories()) + 1


def _other_index() -> int:
    return len(ON.veg_categories())


MIN_TERRAIN_SHARE = 0.01   # retained for signature compatibility (presence is now the criterion).


def terrain_category_counts() -> np.ndarray:
    """Float [10 × C]: 1.0 where a category is native to a terrain in the ontology (incl. the
    terrain-independent 'land'/'water' bucket), else 0. The trailing OTHER column is 0."""
    M = np.array(ON.category_terrain_matrix(), dtype=np.float64)   # [10 × (C-1)]
    return np.concatenate([M, np.zeros((M.shape[0], 1))], axis=1)  # append OTHER column


def allowed_matrix(min_share: float = MIN_TERRAIN_SHARE) -> np.ndarray:
    """Bool [10 terrains × C]: ``allowed[t, c]`` iff category ``c`` is native to terrain ``t`` in the
    ontology (the authoritative terrain coupling). The OTHER channel is never allowed."""
    A = np.array(ON.category_terrain_matrix(), dtype=bool)         # [10 × (C-1)]
    return np.concatenate([A, np.zeros((A.shape[0], 1), dtype=bool)], axis=1)


def category_of(identity: dict) -> int:
    """Channel index for a decoration identity, via the ontology (OTHER if uncategorisable)."""
    idx = ON.category_of(identity.get("animation") or "")
    return idx if idx is not None else _other_index()


def decode_identity(cat_idx: int, terrain_id: int, rng: random.Random) -> dict | None:
    """Concrete {type,subtype,animation,mask} for a category on a terrain, from the ontology.

    Returns None for the OTHER channel or when the category has no ontology decoration native to
    the terrain (caller skips the gene)."""
    if cat_idx >= _other_index():
        return None
    return ON.decode_identity(cat_idx, terrain_id, rng)


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------

def _dist_to_other_terrain(terr: np.ndarray, land: np.ndarray) -> np.ndarray:
    """Per-land-tile proximity to the nearest land tile of a *different* terrain id.

    Multi-source BFS seeded at "seam" tiles (a land tile 4-adjacent to a land tile of a
    different terrain). Returned as ``1 - min(dist/DIST_OTHER_CAP, 1)`` so it is ≈1 on a
    terrain/terrain seam and decays to 0 inland; 0 off land. Water/rock borders are NOT
    seams (that is what ``depth`` already encodes)."""
    H, W = terr.shape
    INF = np.int32(1 << 30)
    dist = np.full((H, W), INF, dtype=np.int32)
    dq = collections.deque()
    nb = ((1, 0), (-1, 0), (0, 1), (0, -1))
    for y in range(H):
        for x in range(W):
            if land[y, x] <= 0:
                continue
            t = terr[y, x]
            for dx, dy in nb:
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H and land[ny, nx] > 0 and terr[ny, nx] != t:
                    dist[y, x] = 0
                    dq.append((x, y))
                    break
    while dq:
        x, y = dq.popleft()
        d = dist[y, x]
        for dx, dy in nb:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and land[ny, nx] > 0 and dist[ny, nx] > d + 1:
                dist[ny, nx] = d + 1
                dq.append((nx, ny))
    prox = np.where(dist < INF, 1.0 - np.minimum(dist / DIST_OTHER_CAP, 1.0), 0.0)
    return (prox * (land > 0)).astype(np.float32)


def _edge_proximity(H: int, W: int) -> np.ndarray:
    """Per-tile proximity to the map raster border: ``1 - min(border_dist/EDGE_CAP, 1)``
    (≈1 on the outer rim, 0 well inside)."""
    ys = np.minimum(np.arange(H), H - 1 - np.arange(H))
    xs = np.minimum(np.arange(W), W - 1 - np.arange(W))
    border = np.minimum(ys[:, None], xs[None, :]).astype(np.float32)
    return (1.0 - np.minimum(border / EDGE_CAP, 1.0)).astype(np.float32)


def _as_terr_array(grid) -> np.ndarray:
    """Accept either an int ndarray, a list-of-list of ints, or the faithful/generated
    list-of-list of ``{"t": id}`` cell dicts, and return an H×W int16 terrain-id array."""
    if isinstance(grid, np.ndarray):
        return grid.astype(np.int16)
    first = grid[0][0]
    if isinstance(first, dict):
        return np.array([[c["t"] for c in row] for row in grid], dtype=np.int16)
    return np.array(grid, dtype=np.int16)


def compute_cond_layers(terr, canon: dict, zones=None, zone_label=None) -> dict:
    """Full-map per-tile condition layers shared by training windows and inference.

    ``terr`` is the H×W terrain-id grid (int array or ``{"t":id}`` cell dicts; water=8/rock=9),
    ``canon`` maps zid -> {(x,y):(d,s)} (per-zone depth/sweep). ``zones``/``zone_label``
    (optional) drive the inter-zone passage layer via :func:`zone_engine._zone_passages`.
    Returns a dict of H×W float32 arrays: ``terr, depth, sweep, land, dist_other, edge,
    passage``."""
    terr = _as_terr_array(terr)
    H, W = terr.shape
    depth = np.zeros((H, W), dtype=np.float32)
    sweep = np.zeros((H, W), dtype=np.float32)
    land = np.zeros((H, W), dtype=np.float32)
    for zid, zc in canon.items():
        for (x, y), (d, s) in zc.items():
            depth[y, x] = d
            sweep[y, x] = s
            land[y, x] = 1.0

    passage = np.zeros((H, W), dtype=np.float32)
    if zones is not None and zone_label is not None:
        for zid, tiles in ZE._zone_passages(zones, zone_label, W, H).items():
            for (x, y) in tiles:
                if 0 <= x < W and 0 <= y < H:
                    passage[y, x] = 1.0

    return {
        "terr": terr,
        "depth": depth,
        "sweep": sweep,
        "land": land,
        "dist_other": _dist_to_other_terrain(terr, land),
        "edge": _edge_proximity(H, W),
        "passage": passage,
    }


def _map_layers(name: str, level: int = 0):
    """Condition layers + decoration anchors for one corpus map level.

    Returns ``(layers, anchors, W, H)`` where ``layers`` is :func:`compute_cond_layers`'s dict
    and ``anchors`` is a list of ``(x, y, category_idx)`` DECORATION anchor tiles."""
    fm = OR.load_faithful(name)
    if level >= len(fm["terrain"]):
        return None
    grid = fm["terrain"][level]
    H, W = len(grid), len(grid[0])
    terr = np.array([[grid[y][x]["t"] for x in range(W)] for y in range(H)], dtype=np.int16)

    zones, zone_label, canon = ZE._segment_level(grid)
    layers = compute_cond_layers(terr, canon, zones, zone_label)

    anchors = []
    for o in fm["objects"]:
        if o.get("l", 0) != level or OR.purpose_of(o) != "DECORATION":
            continue
        x, y = o["x"], o["y"]
        if 0 <= x < W and 0 <= y < H:
            anchors.append((x, y, category_of(OR.exact_identity(o))))
    return layers, anchors, W, H


def _encode_cond(layers: dict, x0, y0, nh, nw=None) -> np.ndarray:
    """COND_CH×nh×nw condition window starting at (x0, y0) (``nw`` defaults to ``nh``).
    Off-map tiles read as non-land AND map-edge (so the generator distinguishes "beyond
    the map" from interior water)."""
    nw = nh if nw is None else nw
    terr = layers["terr"]
    depth, sweep, land = layers["depth"], layers["sweep"], layers["land"]
    dist_other, edge, passage = layers["dist_other"], layers["edge"], layers["passage"]
    H, W = terr.shape
    cond = np.zeros((COND_CH, nh, nw), dtype=np.float32)
    for dy in range(nh):
        y = y0 + dy
        for dx in range(nw):
            x = x0 + dx
            if not (0 <= x < W and 0 <= y < H):
                cond[_C_NONLAND, dy, dx] = 1.0
                cond[_C_EDGE, dy, dx] = 1.0
                continue
            t = int(terr[y, x])
            if t in LAND_TERRAINS:
                cond[t, dy, dx] = 1.0
            else:
                cond[_C_NONLAND, dy, dx] = 1.0
            cond[_C_DEPTH, dy, dx] = depth[y, x]
            cond[_C_SWEEP, dy, dx] = sweep[y, x]
            cond[_C_LANDMASK, dy, dx] = land[y, x]
            cond[_C_DIST_OTHER, dy, dx] = dist_other[y, x]
            cond[_C_EDGE, dy, dx] = edge[y, x]
            cond[_C_PASSAGE, dy, dx] = passage[y, x]
    return cond


def cond_to_arrays(cond: np.ndarray):
    """Inverse of the condition encoding: cond[COND_CH,H,W] -> (terrain_grid, depth, land_mask).

    terrain_grid is the argmax land terrain id where land, else WATER.
    """
    cond = np.asarray(cond, dtype=np.float32)
    land = cond[_C_LANDMASK] > 0.5
    depth = cond[_C_DEPTH]
    terr = np.where(land, cond[:8].argmax(axis=0).astype(int), WATER)
    return terr, depth, land


def iter_windows(seed: int = 0, n: int = N, stride: int = STRIDE,
                 names: list[str] | None = None, max_windows: int | None = None,
                 level: int = 0):
    """Yield (cond[COND_CH,n,n] float32, veg[C,n,n] float32) windows from the corpus.

    Windows with fewer than MIN_LAND land tiles are skipped. If ``max_windows`` is set,
    the full deterministic window list is shuffled with ``seed`` and truncated.
    """
    C = num_categories()
    names = names if names is not None else OR.all_map_names()
    windows = []  # (name_idx, x0, y0) — kept light; encoded on demand below
    layers_cache = {}
    for name in names:
        ml = _map_layers(name, level)
        if ml is None:
            continue
        layers, anchors, W, H = ml
        layers_cache[name] = ml
        land = layers["land"]
        for y0 in range(0, max(1, H - n + 1), stride):
            for x0 in range(0, max(1, W - n + 1), stride):
                if land[y0:y0 + n, x0:x0 + n].sum() >= MIN_LAND:
                    windows.append((name, x0, y0))

    if max_windows is not None and len(windows) > max_windows:
        random.Random(seed).shuffle(windows)
        windows = windows[:max_windows]

    for name, x0, y0 in windows:
        layers, anchors, W, H = layers_cache[name]
        cond = _encode_cond(layers, x0, y0, n)
        veg = np.zeros((C, n, n), dtype=np.float32)
        for (ax, ay, c) in anchors:
            if x0 <= ax < x0 + n and y0 <= ay < y0 + n:
                veg[c, ay - y0, ax - x0] = 1.0
        yield cond, veg


def _windows_cache_path(n: int, stride: int) -> str:
    # COND_CH in the name so a channel-layout change invalidates the stale cache.
    return os.path.join(ROOT, "out", f"veg_windows_n{n}_s{stride}_c{COND_CH}.npz")


def load_windows(seed: int = 0, n: int = N, stride: int = STRIDE,
                 max_windows: int | None = None, cache: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Materialize all windows into (conds[K,COND_CH,n,n], vegs[K,C,n,n]) float32 arrays.

    The full (uncapped) window set is cached to ``out/veg_windows_n{n}_s{stride}.npz`` so repeated
    training runs skip the expensive corpus re-segmentation. ``max_windows`` returns a seeded
    subset of the cached full set (no separate cache)."""
    cpath = _windows_cache_path(n, stride)
    if cache and os.path.exists(cpath):
        d = np.load(cpath)
        conds, vegs = d["conds"], d["vegs"]
    else:
        cl, vl = [], []
        for cond, veg in iter_windows(seed=seed, n=n, stride=stride, max_windows=None):
            cl.append(cond); vl.append(veg)
        if not cl:
            return (np.zeros((0, COND_CH, n, n), np.float32),
                    np.zeros((0, num_categories(), n, n), np.float32))
        conds, vegs = np.stack(cl), np.stack(vl)
        if cache:
            os.makedirs(os.path.dirname(cpath), exist_ok=True)
            np.savez(cpath, conds=conds, vegs=vegs)
    if max_windows is not None and conds.shape[0] > max_windows:
        idx = np.arange(conds.shape[0])
        np.random.default_rng(seed).shuffle(idx)
        idx = idx[:max_windows]
        conds, vegs = conds[idx], vegs[idx]
    return conds, vegs


class WindowDataset:
    """torch Dataset over materialized windows (torch imported lazily here)."""

    def __init__(self, seed: int = 0, n: int = N, stride: int = STRIDE,
                 max_windows: int | None = None):
        import torch  # noqa: F401  (lazy)
        self._torch = torch
        conds, vegs = load_windows(seed=seed, n=n, stride=stride, max_windows=max_windows)
        self.conds = torch.from_numpy(conds)
        self.vegs = torch.from_numpy(vegs)

    def __len__(self):
        return self.conds.shape[0]

    def __getitem__(self, i):
        return self.conds[i], self.vegs[i]


if __name__ == "__main__":
    force = "--rebuild" in sys.argv
    tax = build_taxonomy(force=force)
    cats = tax["categories"]
    print(f"taxonomy: {len(cats)} categories -> out/veg_taxonomy.json")
    print("categories:", cats)
    conds, vegs = load_windows(max_windows=None)
    print(f"windows: {conds.shape[0]}  cond shape {conds.shape[1:]}  veg shape {vegs.shape[1:]}")
    if conds.shape[0]:
        print(f"mean land fraction: {conds[:, _C_LANDMASK].mean():.3f}  "
              f"mean veg anchors/window: {vegs.sum(axis=(1,2,3)).mean():.1f}")
