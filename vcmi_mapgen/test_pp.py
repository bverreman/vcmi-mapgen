"""Reliability tests for the marked-point-process pipeline (pp_stats / pp_sample / macro_topo).

Fast + hermetic where possible: the pair-denominator estimator is checked against brute force
on a synthetic zone; sampling and macro generation are checked for determinism and the spec's
acceptance gates. Tests needing the fitted corpus stats skip when data/pp is absent.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pp_stats as PS           # noqa: E402

HAVE_STATS = os.path.exists(os.path.join(PS.PP_DIR, "veg_grass.json"))
needs_stats = pytest.mark.skipif(not HAVE_STATS, reason="data/pp stats not mined")


def test_pair_denominator_matches_bruteforce():
    """D[r] (numpy shifted-mask count) == brute-force ordered pair count at Chebyshev r."""
    ts = {(x, y) for x in range(7) for y in range(5)} | {(9, 2), (10, 2)}
    D = PS._pair_denominator(ts)
    for r in range(PS.RMAX + 1):
        if r == 0:
            brute = len(ts)
        else:
            brute = sum(1 for a in ts for b in ts
                        if max(abs(a[0] - b[0]), abs(a[1] - b[1])) == r)
        assert D[r] == brute, f"ring {r}: {D[r]} != {brute}"


def test_ring_offsets_partition_window():
    """Rings 0..RMAX exactly tile the (2R+1)^2 window."""
    seen = {(0, 0)}
    for r in range(1, PS.RMAX + 1):
        offs = PS._OFFS[r]
        assert len(offs) == 8 * r
        seen.update(offs)
    assert len(seen) == (2 * PS.RMAX + 1) ** 2


@needs_stats
def test_model_and_sampler_deterministic():
    import pp_sample as PP
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
    import ontology as ON
    for o in a1:
        assert ON.has_animation(o["template"]["animation"])
    assert 0.1 < len(b1) / len(ts) < 0.95


@needs_stats
def test_protected_web_stays_open():
    """No blocking cell may land on the protected walkable web (the hard zero)."""
    import obj_resolve as OR
    import pp_sample as PP
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
def test_gameplay_layer_legal_and_deterministic():
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    o1 = PG.place_zone(ts, zones, 1, "grass", seed=4)
    o2 = PG.place_zone(ts, zones, 1, "grass", seed=4)
    assert o1 == o2, "gameplay placement must be seed-deterministic"
    objs, occupied, blocked, approaches = o1
    assert objs, "a 720-tile grass zone should hold gameplay"
    # GUARD monsters deliberately sit ON approaches/gates — the rigid rules below apply to
    # the buildings; guards only need to be in-zone, single-tile, passable-visitable
    core = [o for o in objs if o["purpose"] != "GUARD"]
    for g in (o for o in objs if o["purpose"] == "GUARD"):
        assert (g["x"], g["y"]) in ts and g["template"]["mask"] == ["A"]
        assert g["type"].startswith("randomMonster"), "guards are random monsters"
    # rigid rules: footprints in-zone, no overlap, approach tile free and in-zone
    seen = set()
    for o in core:
        allc, blk, approach = PG._cells({"mask": o["template"]["mask"]}, o["x"], o["y"])
        assert approach is not None and approach in ts
        for cell in allc:
            assert cell in ts and cell not in seen
        seen.update(allc)
    # a hero must be able to stand on every approach tile: never under a blocking cell
    assert set(approaches).isdisjoint(blocked)
    assert blocked <= occupied
    # separation: at least GAP free tiles between any two gameplay footprints
    per_obj = []
    for o in core:
        allc, _b, _a = PG._cells({"mask": o["template"]["mask"]}, o["x"], o["y"])
        per_obj.append(allc)
    for i in range(len(per_obj)):
        for j in range(i + 1, len(per_obj)):
            d = min(max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                    for a in per_obj[i] for b in per_obj[j])
            assert d > PG.GAP, f"objects {i},{j} too close (cheb {d})"


def test_select_player_zones_far_apart():
    """Player zones must be big AND mutually far apart — never all clustered together."""
    import pp_map as PM

    def zone(zid, cx, cy, area):
        return {"tiles_set": [], "centroid": (cx, cy), "area": area, "terrain_type": 2}
    zones = {0: zone(0, 36, 36, 500),                 # big centre
             1: zone(1, 4, 4, 220), 2: zone(2, 68, 4, 200),
             3: zone(3, 4, 68, 200), 4: zone(4, 68, 68, 220),
             5: zone(5, 40, 40, 300),                 # big but right NEXT to the centre
             6: zone(6, 30, 30, 40)}                  # too small: never a start
    picks = PM.select_player_zones(zones, 2)
    assert picks[0] == 0, "first pick is the largest zone"
    assert picks[1] in (1, 2, 3, 4), "second pick is a far corner, not the adjacent zone 5"
    picks4 = PM.select_player_zones(zones, 4)
    assert 6 not in picks4 and 5 not in picks4, "small/adjacent zones lose to far corners"
    cents = [zones[z]["centroid"] for z in picks4]
    dmin = min((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
               for i, a in enumerate(cents) for b in cents[i + 1:])
    assert dmin >= 32 ** 2, "chosen starts keep real distance between them"
    assert PM.select_player_zones(zones, 2) == picks, "selection is deterministic"


@needs_stats
def test_forced_town_sits_on_zone_centroid():
    """A designated player zone gets its town ON the centroid (footprint-centered)."""
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    objs, *_ = PG.place_zone(ts, zones, 1, "grass", seed=4, force_town=True)
    towns = [o for o in objs if o["purpose"] == "TOWN"]
    assert towns, "force_town guarantees a town in a 720-tile zone"
    t = towns[0]
    mh = len(t["mask"])
    mw = max(len(r) for r in t["mask"])
    fx = t["x"] - (mw - 1) / 2.0                     # footprint centre (bottom-right anchor)
    fy = t["y"] - (mh - 1) / 2.0
    assert abs(fx - 14.5) <= 1.5 and abs(fy - 11.5) <= 1.5, \
        f"town footprint centre ({fx},{fy}) must sit on the centroid (14.5,11.5)"


@needs_stats
def test_pickup_layer_legal_and_deterministic():
    import pp_gameplay as PG
    import pp_pickup as PK
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    # synthetic open field with a sealed-off pocket-ish structure: a web cross + nooks
    prot = {(x, 12) for x in range(30)} | {(15, y) for y in range(24)}
    open_set = set(ts)
    o1 = PK.place_pickups(ts, zones, 1, "grass", open_set, prot, seed=6)
    o2 = PK.place_pickups(ts, zones, 1, "grass", open_set, prot, seed=6)
    assert o1 == o2, "pickup layer must be seed-deterministic"
    assert o1, "a 720-tile grass zone should hold pickups"
    used = set()
    for o in o1:
        cells = PK._legal({"mask": o["template"]["mask"]}, o["x"], o["y"], open_set, set())
        assert cells is not None, "footprint must lie on open tiles"
        assert used.isdisjoint(cells), "pickups must not overlap each other"
        used.update(cells)
        if o["purpose"] == "GUARD":
            assert (o["x"], o["y"]) not in prot, "guards must not sit on the mandatory web"


def test_g2_repair_carves_vegetation_only():
    """A vegetation wall between two pickups gets carved; gameplay walls do not."""
    import pp_map as PM
    size = 12
    grid = [[2] * size for _ in range(size)]         # all grass
    veg_wall = [{"x": 6, "y": y, "l": 0, "type": "pineTrees", "subtype": "pineTrees",
                 "mask": ["B"], "template": {"animation": "avlpn0", "mask": ["B"]}}
                for y in range(size)]
    picks = [{"x": 2, "y": 5, "l": 0, "purpose": "RESOURCE_PILE", "type": "resource",
              "subtype": "wood", "mask": ["A"],
              "template": {"animation": "avtwood0", "mask": ["A"]}},
             {"x": 10, "y": 5, "l": 0, "purpose": "RESOURCE_PILE", "type": "resource",
              "subtype": "ore", "mask": ["A"],
              "template": {"animation": "avtore0", "mask": ["A"]}}]
    objs, removed = PM.g2_repair(size, grid, veg_wall + picks, [(2, 5), (10, 5)])
    assert removed >= 1, "the veg wall must be carved"
    _, removed2 = PM.g2_repair(size, grid, objs, [(2, 5), (10, 5)])
    assert removed2 == 0, "after repair the map must be G2-clean"
    # same wall as GAMEPLAY (purpose set) must NOT be carved
    hard_wall = [dict(o, purpose="DWELLING") for o in veg_wall]
    objs3, removed3 = PM.g2_repair(size, grid, hard_wall + picks, [(2, 5), (10, 5)])
    assert removed3 == 0 and len(objs3) == len(hard_wall) + len(picks)


def test_vmap_export_roundtrip():
    """export_vmap writes an editor-shaped .vmap: reads back, visitables carry
    visitableFrom, and a playable slot is wired to the town."""
    import glob
    import json
    import zipfile
    if not glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap"):
        pytest.skip("VCMI template .vmap not available")
    import ontology as ON
    import pp_map as PM
    import zone_engine as ZE
    grid = [[2] * 16 for _ in range(16)]
    cells = ZE.tile_terrain(grid, 16, 16)
    town = ON.gameplay_pool("grass", "TOWN")[0]
    objs = [{"x": 8, "y": 8, "l": 0, "purpose": "TOWN", "type": town["type"],
             "subtype": town["subtype"], "animation": town["animation"],
             "mask": town["mask"],
             "template": {"animation": town["animation"], "mask": town["mask"]}}]
    out = os.path.join(os.path.dirname(PS.PP_DIR), "..", "out", "vmap", "test_pp_export.vmap")
    p = PM.export_vmap(cells, objs, os.path.abspath(out), name="test")
    z = zipfile.ZipFile(p)
    surf = json.loads(z.read("surface_terrain.json"))
    vobjs = json.loads(z.read("objects.json").decode())
    header = json.loads(z.read("header.json").decode())
    assert len(surf) == 16 and len(vobjs) == 1
    assert vobjs[0]["template"].get("visitableFrom"), "town must carry visitableFrom"
    wired = [pl for pl in header["players"].values()
             if isinstance(pl, dict) and pl.get("mainTown")]
    assert wired, "a player slot must be wired to the town"


def test_parse_teams():
    import pp_map as PM
    assert PM.parse_teams("ffa", 3) == [0, 1, 2]
    assert PM.parse_teams("2v2", 4) == [0, 0, 1, 1]
    assert PM.parse_teams("1v3", 4) == [0, 1, 1, 1]
    assert PM.parse_teams("0,0,1,1", 4) == [0, 0, 1, 1]
    with pytest.raises(ValueError):
        PM.parse_teams("2v2", 3)


def test_playability_overlay():
    """apply_playability: exactly N playable slots wired to their towns, team matrix set,
    victory = defeat all (standardWin)."""
    import glob
    import json
    import zipfile
    if not glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap"):
        pytest.skip("VCMI template .vmap not available")
    import ontology as ON
    import pp_map as PM
    import zone_engine as ZE
    grid = [[2] * 24 for _ in range(24)]
    cells = ZE.tile_terrain(grid, 24, 24)
    town = ON.gameplay_pool("grass", "TOWN")[0]
    towns = [{"x": 8, "y": 8, "l": 0, "purpose": "TOWN", "type": town["type"],
              "subtype": town["subtype"], "animation": town["animation"],
              "mask": town["mask"],
              "template": {"animation": town["animation"], "mask": town["mask"]}},
             {"x": 18, "y": 18, "l": 0, "purpose": "TOWN", "type": town["type"],
              "subtype": town["subtype"], "animation": town["animation"],
              "mask": town["mask"],
              "template": {"animation": town["animation"], "mask": town["mask"]}}]
    out = os.path.join(os.path.dirname(PS.PP_DIR), "..", "out", "vmap", "test_pp_play.vmap")
    p = PM.export_vmap(cells, towns, os.path.abspath(out), name="test")
    PM.apply_playability(p, towns, teams=[0, 1])
    h = json.loads(zipfile.ZipFile(p).read("header.json").decode())
    playable = {pid: pl for pid, pl in h["players"].items()
                if isinstance(pl, dict) and pl.get("canPlay") == "PlayerOrAI"}
    assert len(playable) == 2, "exactly N playable slots"
    wired = sorted((pl["mainTown"]["x"], pl["mainTown"]["y"]) for pl in playable.values())
    assert wired == [(6, 6), (16, 16)], "each player wired to its designated town (-2 offset)"
    assert sorted(pl["team"] for pl in playable.values()) == [0, 1]
    for pid, pl in h["players"].items():
        if isinstance(pl, dict) and pid not in playable:
            assert pl.get("canPlay") == "false" and pl.get("mainTown") is None
    tv = h["triggeredEvents"]
    assert set(tv) == {"standardVictory", "standardDefeat"}, "no special conditions"
    assert tv["standardVictory"]["condition"][0] == "standardWin", "victory = defeat all"
    # THE ownership fix: the town OBJECTS carry options.owner (mainTown alone is not enough)
    vobjs = json.loads(zipfile.ZipFile(p).read("objects.json").decode())
    owners = sorted(o["options"]["owner"] for o in vobjs
                    if o.get("type") in ("town", "randomTown")
                    and o.get("options", {}).get("owner"))
    assert len(owners) == 2 and owners == sorted(playable), \
        "each player town must be owned by its player"


def test_macro_generate_deterministic_and_coarse():
    import macro_topo as MT
    g1 = MT.generate(48, 48, seed=1)
    g2 = MT.generate(48, 48, seed=1)
    assert g1 == g2, "macro terrain must be seed-deterministic"
    rep = MT.report(g1)
    # the §4.3 gate: the macro layer must NOT fragment (the markov failure mode)
    assert rep["big_share"] >= 0.7, rep
