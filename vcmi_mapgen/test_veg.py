"""Smoke tests for the learned-vegetation pipeline (taxonomy / windowing / GAN / GA decode).

Kept fast and tiny: the GAN tests run on a synthetic 16×16 batch for 1–2 steps. They are on a
separate code path from the bit-exact identity rebuild and render-determinism tests, which are
untouched. torch tests skip gracefully if torch is unavailable.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veg_data as VD
import veg_decode as DEC


def test_taxonomy_categories():
    cats = VD.categories()
    assert len(cats) >= 10 and cats[-1] == "OTHER"
    assert VD.num_categories() == len(cats)
    # categories are the ONTOLOGY decoration type-level keys; a known decoration animation maps
    # to its own (non-OTHER) channel (case-insensitive), and an unknown animation -> OTHER.
    idx = VD.category_of({"type": "mountain", "subtype": "object",
                          "animation": "AVLMTSN1", "mask": ["B"]})
    assert cats[idx] == "MOUNTAIN"
    assert VD.category_of({"animation": "no_such_def_xyz"}) == cats.index("OTHER")


def test_window_shapes():
    cond, veg = next(VD.iter_windows(max_windows=4))
    assert cond.shape == (VD.COND_CH, VD.N, VD.N)
    assert veg.shape == (VD.num_categories(), VD.N, VD.N)
    assert cond.dtype == np.float32 and veg.dtype == np.float32
    assert set(np.unique(veg)).issubset({0.0, 1.0})


def test_cond_to_arrays_roundtrip():
    cond, _ = next(VD.iter_windows(max_windows=4))
    terr, depth, land = VD.cond_to_arrays(cond)
    assert terr.shape == land.shape == (VD.N, VD.N)
    # every land tile carries a land terrain id (0..7)
    assert ((terr[land] >= 0) & (terr[land] < 8)).all()


def test_decode_valid_objects():
    import obj_resolve as OR
    C = VD.num_categories()
    H = W = 20
    rng = np.random.default_rng(0)
    field = (rng.random((C, H, W)).astype("float32") * 0.5)
    field[VD.category_of({"type": "mountain"})] += 0.45
    land = np.ones((H, W), bool)
    gh = np.zeros((H, W), bool)
    gh[8:10, 8:10] = True  # a planned-gameplay block decoration must not bury
    depth = rng.random((H, W)).astype("float32")
    terr = np.full((H, W), 2, dtype=int)
    objs = DEC.decode_zone(field, land, gh, depth, terr, seed=0, level=0, pop=16, gens=8)
    assert objs, "expected some vegetation placed"
    for o in objs:
        assert o["_purpose"] == "DECORATION"
        for tx, ty, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if blk:
                assert 0 <= tx < W and 0 <= ty < H          # in-bounds
                assert land[ty, tx] and not gh[ty, tx]       # on land, never buries gameplay
    # deterministic for a fixed seed
    objs2 = DEC.decode_zone(field, land, gh, depth, terr, seed=0, level=0, pop=16, gens=8)
    assert [(o["type"], o["x"], o["y"]) for o in objs] == \
           [(o["type"], o["x"], o["y"]) for o in objs2]


def test_cond_layers_seam_and_edge():
    # two terrains split down the middle; all land, depth/sweep flat.
    H = W = 16
    terr = np.zeros((H, W), dtype=int)
    terr[:, W // 2:] = 1
    canon = {0: {(x, y): (0.0, 0.0) for y in range(H) for x in range(W // 2)},
             1: {(x, y): (0.0, 0.0) for y in range(H) for x in range(W // 2, W)}}
    layers = VD.compute_cond_layers(terr, canon)
    do, edge = layers["dist_other"], layers["edge"]
    # dist-to-other-terrain is ≈1 on the seam columns, ~0 deep inside a terrain
    assert do[H // 2, W // 2 - 1] > 0.9 and do[H // 2, W // 2] > 0.9
    assert do[H // 2, 0] < 0.2 and do[H // 2, W - 1] < 0.2
    # map-edge proximity is 1 at the corner/border, ~0 in the interior
    assert edge[0, 0] > 0.99 and edge[H // 2, W // 2] < 0.2
    for k in ("terr", "depth", "sweep", "land", "dist_other", "edge", "passage"):
        a = np.asarray(layers[k], dtype=float)
        if k != "terr":
            assert a.min() >= 0.0 and a.max() <= 1.0


def test_passage_tile_never_blocked():
    # a protected one-tile passage must keep a clear gap even when the field wants a
    # mountain wall everywhere (decode_zone treats protected tiles like gameplay-hard).
    import obj_resolve as OR
    C = VD.num_categories()
    H = W = 16
    rng = np.random.default_rng(1)
    field = rng.random((C, H, W)).astype("float32") * 0.3
    field[VD.category_of({"type": "mountain"})] += 0.6  # push lots of blocking mountains
    land = np.ones((H, W), bool)
    passage = (8, 8)
    gh = np.zeros((H, W), bool)
    gh[passage[1], passage[0]] = True                   # protect the passage tile
    depth = np.zeros((H, W), "float32")
    terr = np.full((H, W), 2, dtype=int)
    objs = DEC.decode_zone(field, land, gh, depth, terr, seed=0, level=0, pop=24, gens=12)
    for o in objs:
        for tx, ty, blk in OR.mask_cells(o["mask"], o["x"], o["y"]):
            if blk:
                assert (tx, ty) != passage, "a blocking footprint covered the protected passage"


def test_train_one_step():
    torch = pytest.importorskip("torch")
    import veg_gan as VG
    C = VD.num_categories()
    n = 16
    VG.seed_everything(0)
    G = VG.VegGenerator(veg_ch=C, width=16)
    D = VG.VegDiscriminator(veg_ch=C, width=16)
    cond = torch.rand(4, VD.COND_CH, n, n)
    veg = (torch.rand(4, C, n, n) > 0.9).float()
    import torch.nn.functional as F
    optG = torch.optim.Adam(G.parameters(), 2e-4)
    optD = torch.optim.Adam(D.parameters(), 2e-4)
    for _ in range(2):
        noise = torch.randn(4, VG.NOISE_CH, n, n)
        with torch.no_grad():
            vf = VG.to_field(G(cond, noise))
        dr, _ = D(cond, veg); df, _ = D(cond, vf)
        lossD = F.relu(1 - dr).mean() + F.relu(1 + df).mean()
        optD.zero_grad(); lossD.backward(); optD.step()
        vf = VG.to_field(G(cond, noise)); df, ff = D(cond, vf)
        with torch.no_grad():
            _, rf = D(cond, veg)
        lossG = -df.mean() + VG.FM_LAMBDA * VG._feature_match(rf, ff)
        optG.zero_grad(); lossG.backward(); optG.step()
        assert torch.isfinite(lossD) and torch.isfinite(lossG)
    field = VG.sample_field(G, cond[0].numpy(), seed=0)
    assert field.shape == (C, n, n)
    assert float(field.min()) >= 0.0 and float(field.max()) <= 1.0
