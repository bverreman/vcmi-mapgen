"""Reliability tests for steps.vegetation.sample (marked-point-process vegetation sampler)."""
import os

import pytest

from vcmi_mapgen.steps.vegetation import stats as PS

HAVE_STATS = os.path.exists(os.path.join(PS.PP_DIR, "veg_grass.json"))
needs_stats = pytest.mark.skipif(not HAVE_STATS, reason="data/pp stats not mined")


@needs_stats
def test_model_and_sampler_deterministic():
    from vcmi_mapgen.steps.vegetation import sample as PP
    model = PP.build_model("grass")
    assert model["cats"], "grass model has categories"
    assert 0 < model["target"] < 1
    ts = {(x, y) for x in range(18) for y in range(14)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (8.5, 6.5), "area": len(ts),
                 "terrain_type": 2}}
    a1, b1, _ = PP.sample_zone(ts, zones, 1, model, seed=5)
    a2, b2, _ = PP.sample_zone(ts, zones, 1, model, seed=5)
    assert a1 == a2 and b1 == b2, "same seed must reproduce bit-exactly"
    assert a1, "some vegetation sampled"
    # every mask comes from the ontology and coverage is sane
    from vcmi_mapgen import ontology as ON
    for o in a1:
        assert ON.has_animation(o["template"]["animation"])
    assert 0.1 < len(b1) / len(ts) < 0.95


@needs_stats
def test_protected_web_stays_open():
    """No blocking cell may land on the protected walkable web (the hard zero)."""
    from vcmi_mapgen import obj_resolve as OR
    from vcmi_mapgen.steps.vegetation import sample as PP
    model = PP.build_model("grass")
    ts = {(x, y) for x in range(20) for y in range(16)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (9.5, 7.5), "area": len(ts),
                 "terrain_type": 2}}
    objs, blocked, prot = PP.sample_zone(ts, zones, 1, model, seed=9)
    assert prot, "web exists"
    for o in objs:
        for cx, cy, blk in OR.mask_cells(o["template"]["mask"], o["x"], o["y"]):
            if blk:
                assert (cx, cy) not in prot
    assert not (blocked & prot)


@needs_stats
def test_protected_web_covers_gate_bands():
    from vcmi_mapgen.steps.vegetation import sample as PP
    from vcmi_mapgen import zone_field as ZF
    ts1 = {(x, y) for x in range(14) for y in range(12)}
    ts2 = {(x, y) for x in range(14, 28) for y in range(12)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (6.5, 5.5), "area": 168,
                 "terrain_type": 2},
             2: {"tiles_set": sorted(ts2), "centroid": (20.5, 5.5), "area": 168,
                 "terrain_type": 3}}
    edist = ZF.edge_dist(ts1)
    prot = PP.protected_web(ts1, zones, 1, edist, (6, 5), open_frac=0.5)
    for rep, band in ZF._zone_gate_bands(ts1, zones, 1, open_frac=0.5):
        assert band <= prot, "every gate-band tile must be protected from vegetation"


@needs_stats
def test_border_bias_densifies_front():
    """Zone isolation: with BOTH zones sampling under the `border=` bias, the only aligned
    open crossings left between them are the planned entrance band — each single side is
    only a partial ridge (Geyer saturation caps clumping), but the seal is 2-thick."""
    from vcmi_mapgen.steps.vegetation import sample as PP
    from vcmi_mapgen import zone_field as ZF

    ts1 = {(x, y) for x in range(14) for y in range(12)}
    ts2 = {(x, y) for x in range(14, 28) for y in range(12)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (6.5, 5.5), "area": 168,
                 "terrain_type": 2},
             2: {"tiles_set": sorted(ts2), "centroid": (20.5, 5.5), "area": 168,
                 "terrain_type": 2}}
    plan = ZF.plan_entrances(zones)
    model = PP.build_model("grass")

    def zone_pass(zid, ts, seed, border_bias=True):
        z_entr = plan[zid]
        edist = ZF.edge_dist(ts)
        c = zones[zid]["centroid"]
        seedt = min(ts, key=lambda t: (t[0] - int(round(c[0]))) ** 2
                    + (t[1] - int(round(c[1]))) ** 2)
        prot = PP.protected_web(ts, zones, zid, edist, seedt, entrances=z_entr)
        front = set().union(*ZF._zone_fronts(ts, zones, zid).values())
        bands = set().union(*(b for _r, b, _o in z_entr))
        border = frozenset(front - bands) if border_bias else frozenset()
        _, blk, _ = PP.sample_zone(ts, zones, zid, model, seed=seed, prot=prot,
                                   border=border)
        return blk, front, bands, frozenset(front - bands)

    for seed in (3, 7):
        blk1, f1, b1, border1 = zone_pass(1, ts1, seed)
        blk2, _f2, b2, _ = zone_pass(2, ts2, seed)
        assert not (blk1 & b1) and not (blk2 & b2), "entrance bands stay vegetation-free"
        open_all = (ts1 - blk1) | (ts2 - blk2)
        crossings = {t for t in f1
                     if t in open_all and (t[0] + 1, t[1]) in open_all}
        assert crossings, "the planned entrance must stay open"
        assert crossings <= b1, \
            f"every crossing must be a planned entrance, leaks: {sorted(crossings - b1)}"
        # the bias densifies the front vs the unbiased sampler on the same seed
        blk_plain, *_ = zone_pass(1, ts1, seed, border_bias=False)
        assert len(blk1 & border1) > len(blk_plain & border1), \
            "border bias must densify the front"
        # coverage correction keeps TOTAL density corpus-like (redistribution, not inflation)
        assert len(blk1) / len(ts1) < model["target"] + 0.2
