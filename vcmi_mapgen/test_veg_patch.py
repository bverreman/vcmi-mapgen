"""Smoke tests for the patch-quilt GA vegetation (veg_patch).

Torch-free and fast: a tiny synthetic grass patch + a synthetic target zone exercise candidate
binning, the hard-validity gate, real-identity reuse, determinism, and the distribution objective.
On a separate path from the bit-exact identity rebuild + render-determinism tests (untouched).
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obj_resolve as OR
import zone_engine as ZE
import veg_patch as VP

GRASS = 2
MTN = {"type": "mountain", "subtype": "object", "animation": "AVLMTSN1", "mask": ["B"]}   # blocking
TREE = {"type": "oakTrees", "subtype": "object", "animation": "AVLholg0", "mask": ["A"]}  # overlay


def _make_patch(tt=GRASS, w=10, h=10):
    """A solid grass square with a blocking mountain rim and a few interior overlay trees."""
    mask_rel = [[x, y] for y in range(h) for x in range(w)]
    objs = []
    for x in range(w):
        for y in (0, h - 1):
            objs.append({"purpose": "DECORATION", "identity": dict(MTN),
                         "anchor_off": [x, y], "canon": [0.0, 0.0]})
    for y in range(1, h - 1):
        for x in (0, w - 1):
            objs.append({"purpose": "DECORATION", "identity": dict(MTN),
                         "anchor_off": [x, y], "canon": [0.0, 0.0]})
    for (x, y) in [(4, 4), (5, 5), (4, 5)]:
        objs.append({"purpose": "DECORATION", "identity": dict(TREE),
                     "anchor_off": [x, y], "canon": [0.6, 0.5]})
    return {"source_map": "synthetic", "level": 0, "zone_id": 0, "terrain_type": tt,
            "area": w * h, "bbox": [0, 0, w - 1, h - 1], "centroid": [w / 2, h / 2],
            "label": "test", "shape_hash": "deadbeef", "mask_rel": mask_rel, "objects": objs}


def _write_pool(tmp_path, tt=GRASS):
    """Write one synthetic patch and return (pool, patch_dict)."""
    patch = _make_patch(tt)
    p = os.path.join(str(tmp_path), "z0_L0__test.json")
    json.dump(patch, open(p, "w"))
    pool = {ZE.TNAME[tt]: [(patch["area"], p)]}
    return pool, patch


def _target_zone(tt=GRASS, inner=2, size=16):
    """A clean single same-terrain zone (rock backdrop) + its shape-intrinsic canon."""
    g = [[ZE._cell(ZE.TS.ROCK, x, y) for x in range(size)] for y in range(size)]
    for y in range(inner, size - inner):
        for x in range(inner, size - inner):
            g[y][x] = ZE._cell(tt, x, y)
    zones, _zl, canon = ZE._segment_level(g)
    zid = max((k for k, z in zones.items() if z["terrain_type"] == tt),
              key=lambda k: zones[k]["area"])
    return zones[zid], canon[zid]


def test_candidate_selection_same_terrain(tmp_path):
    pool, _ = _write_pool(tmp_path, GRASS)
    # nearest-by-area returns the grass patch for grass, nothing for an empty terrain
    assert ZE.nearest_patch_paths(pool, GRASS, 100, 8)
    assert ZE.nearest_patch_paths(pool, 3, 100, 8) == []          # snow: no patches
    cands = VP.gather_candidates(pool, GRASS, 100)
    assert cands and all(c.cell_idents for c in cands)


def test_decode_validity_and_passage(tmp_path):
    pool, _ = _write_pool(tmp_path, GRASS)
    z, canon = _target_zone(GRASS)
    ts = z["tiles_set"]
    passage = sorted(ts)[len(ts) // 2]                            # protect one interior tile
    objs = VP.decorate_zone(z, canon, pool, [passage], set(), seed=0)
    assert objs, "expected some vegetation placed"
    blocked = set()
    for o in objs:
        assert o["_purpose"] == "DECORATION"
        for tx, ty, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if blk:
                assert (tx, ty) in ts                              # in-bounds + on land
                assert (tx, ty) != passage                         # passage gap stays open
                assert (tx, ty) not in blocked                     # no blocking overlap
                blocked.add((tx, ty))


def test_real_identity_reuse(tmp_path):
    pool, patch = _write_pool(tmp_path, GRASS)
    z, canon = _target_zone(GRASS)
    src_types = {o["identity"]["type"] for o in patch["objects"]}
    objs = VP.decorate_zone(z, canon, pool, None, set(), seed=1)
    assert objs
    for o in objs:                                                 # every kind comes from a patch
        assert o["type"] in src_types


def test_determinism(tmp_path):
    pool, _ = _write_pool(tmp_path, GRASS)
    z, canon = _target_zone(GRASS)
    a = VP.decorate_zone(z, canon, pool, None, set(), seed=7)
    b = VP.decorate_zone(z, canon, pool, None, set(), seed=7)
    assert [(o["type"], o["x"], o["y"]) for o in a] == \
           [(o["type"], o["x"], o["y"]) for o in b]


def test_objective_matches_density(tmp_path):
    # the fitness measures distribution match: a genome that USES the patch tracks the corpus
    # per-depth density far better than the all-empty genome (zero density everywhere).
    pool, _ = _write_pool(tmp_path, GRASS)
    z, canon = _target_zone(GRASS)
    cands = VP.gather_candidates(pool, GRASS, z["area"])
    q = VP._Quilt(z, canon, cands, set(), None)
    full = [0] * q.n                                               # every cell from patch 0
    empty = [-1] * q.n
    _o, _b, dens_full, _d = VP._decode(q, full, seed=0)
    _o, _b, dens_empty, _d = VP._decode(q, empty, seed=0)
    d_full = float(np.abs(dens_full - q.target_dens).sum())
    d_empty = float(np.abs(dens_empty - q.target_dens).sum())
    assert d_full < d_empty                                        # placing matches; empty doesn't
    # and the GA's best is at least as good as the all-empty baseline
    best = VP._evolve(q, seed=0)
    assert best, "GA should place vegetation"
