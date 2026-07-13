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

import vcmi_paths  # noqa: E402

RANDOMMAPS_GLOB = os.path.join(vcmi_paths.vcmi_home(), "Maps", "RandomMaps", "*.vmap")


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
    # GUARD monsters deliberately sit ON approaches/gates, and MINE_SEAL decorations
    # deliberately sit GAP-adjacent to the mine they seal off (no approach of their own) —
    # the rigid rules below apply to the buildings only.
    core = [o for o in objs if o["purpose"] not in ("GUARD", "MINE_SEAL")]
    for g in (o for o in objs if o["purpose"] == "GUARD"):
        # monster masks are V-padded to the sprite's tile extent (ground truth from
        # Maps/RandomMaps: every creature mask is ['VV', 'VA']), not a bare single cell.
        assert (g["x"], g["y"]) in ts and g["template"]["mask"] == ["VV", "VA"]
        assert g["type"].startswith("randomMonster"), "guards are random monsters"
    for s in (o for o in objs if o["purpose"] == "MINE_SEAL"):
        assert (s["x"], s["y"]) in ts and s["template"]["mask"] == ["B"], \
            "a mine seal is a single blocking cell"
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
    zones_by_level = {0: zones}
    picks = PM.select_player_zones(zones_by_level, 2)
    assert picks[0] == (0, 0), "first pick is the largest zone"
    assert picks[1][1] in (1, 2, 3, 4), "second pick is a far corner, not the adjacent zone 5"
    picks4 = PM.select_player_zones(zones_by_level, 4)
    zids4 = [zid for _l, zid in picks4]
    assert 6 not in zids4 and 5 not in zids4, "small/adjacent zones lose to far corners"
    cents = [zones[zid]["centroid"] for _l, zid in picks4]
    dmin = min((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
               for i, a in enumerate(cents) for b in cents[i + 1:])
    assert dmin >= 32 ** 2, "chosen starts keep real distance between them"
    assert PM.select_player_zones(zones_by_level, 2) == picks, "selection is deterministic"


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
    # player start towns are ALWAYS randomTown: VCMI resolves an owned random town to the
    # lobby faction pick — a concrete start town would override the player's choice
    assert t["type"] == "randomTown", f"forced town must be randomTown, got {t['type']}"
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
    import obj_resolve as OR
    used = set()
    for o in o1:
        if o["purpose"] == "GUARD":
            # a guard's decorative sprite-bleed cells MAY overlap terrain or already-placed
            # cache pickups (the pocket it seals is packed by design); only its interactive
            # cell — the tile the monster actually stands on — must be free and unique
            inter = OR.mask_interactive_cells(o["template"]["mask"], o["x"], o["y"])
            assert inter and used.isdisjoint(inter), "guard stand-tile must be free"
            used.update(inter)
            assert (o["x"], o["y"]) not in prot, "guards must not sit on the mandatory web"
            continue
        cells = PK._legal({"mask": o["template"]["mask"]}, o["x"], o["y"], open_set, set())
        assert cells is not None, "footprint must lie on open tiles"
        assert used.isdisjoint(cells), "pickups must not overlap each other"
        used.update(cells)


def _field(w, h, walls):
    return {(x, y) for x in range(w) for y in range(h)} - set(walls)


def test_find_pockets_drawn_shapes():
    """Regression fixture from the user's own drawings (2026-07-05). A pocket's neck is a
    guard's 3x3 zone of control, NOT a single walkable tile: with H3 diagonal movement a
    1-2 tile nook in a FLAT wall face has three entrance tiles, so the old single-tile
    test could never detect it (it only worked when the flanking walls happened to
    protrude past the face and block the diagonals)."""
    import pp_pickup as PK
    import zone_field as ZF

    def best_mouths(reach, pocket_tiles):
        """canonical (deduped, ranked) mouth candidates whose pocket covers the nook"""
        raw = {m: c for m, c in ZF.find_pockets(reach).items()
               if set(pocket_tiles) <= set(c)}
        return [cands[0] for cands in PK._dedupe_pockets(raw, reach)]

    # Pocket 1: 1-tile nook in a flat wall face, open field above. The canonical guard
    # must stand directly in front (orthogonal), not on a diagonal or a tile out.
    #     . . .
    #     X o X
    #     X X X
    reach = _field(12, 12, {(4, 5), (6, 5), (4, 6), (5, 6), (6, 6)})
    (mouth, pocket), = best_mouths(reach, {(5, 5)})
    assert (mouth, set(pocket)) == ((5, 4), {(5, 5)})

    # 2-tile flat-face nook ("1 or 2 tiles fully surrounded")
    reach = _field(12, 12, {(3, 5), (6, 5), (3, 6), (4, 6), (5, 6), (6, 6)})
    (mouth, pocket), = best_mouths(reach, {(4, 5), (5, 5)})
    assert set(pocket) == {(4, 5), (5, 5)} and mouth in ((4, 4), (5, 4))

    # protruding-corner nook: the one flat-face variant the OLD test also caught --
    # must keep detecting it
    reach = _field(12, 12, {(4, 4), (6, 4), (4, 5), (6, 5), (4, 6), (5, 6), (6, 6)})
    (mouth, pocket), = best_mouths(reach, {(5, 5)})
    assert (mouth, set(pocket)) == ((5, 4), {(5, 5)})

    # Pocket 2: dead-end corridor -- guard at the corridor entrance, treasures behind
    #     X X X X X X
    #     X . . . . .
    #     X X X X X X
    walls = ({(x, 5) for x in range(2, 8)} | {(2, 6)} | {(x, 7) for x in range(2, 8)})
    (mouth, pocket), = best_mouths(_field(14, 14, walls),
                                   {(3, 6), (4, 6), (5, 6), (6, 6)})
    assert (mouth, set(pocket)) == ((7, 6), {(3, 6), (4, 6), (5, 6), (6, 6)})

    # Pocket 3: bent corridor, user's `O` = guard opening, `P` = treasure tiles
    #       X X X X X X
    #     X X X P P P P O
    #     X P P P X X X X
    #     X X X X X
    walls = ({(x, 5) for x in range(5, 11)} | {(x, 6) for x in range(3, 6)} |
             {(3, 7)} | {(x, 7) for x in range(7, 11)} | {(x, 8) for x in range(3, 8)})
    P = {(6, 6), (7, 6), (8, 6), (9, 6), (4, 7), (5, 7), (6, 7)}
    (mouth, pocket), = best_mouths(_field(20, 20, walls), P)
    assert (mouth, set(pocket)) == ((10, 6), P)

    # control: a lone straight wall through an open field must yield no pocket anywhere
    # along its run (its two END corners against the map edge are genuine 1-tile corner
    # nooks and MAY be flagged -- that is accepted semantics, thinned by POCKET_MIN_SEP)
    reach = _field(12, 12, {(x, 6) for x in range(12)})
    for m, c in ZF.find_pockets(reach).items():
        assert all(t[0] in (0, 11) for t in c), f"mid-wall false positive {m}->{sorted(c)}"


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
    if not glob.glob(RANDOMMAPS_GLOB):
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
    p = PM.export_vmap([cells], objs, os.path.abspath(out), name="test")
    z = zipfile.ZipFile(p)
    surf = json.loads(z.read("surface_terrain.json"))
    vobjs = json.loads(z.read("objects.json").decode())
    header = json.loads(z.read("header.json").decode())
    assert len(surf) == 16 and len(vobjs) == 1
    assert vobjs[0]["template"].get("visitableFrom"), "town must carry visitableFrom"
    # VCMI's mask parser only knows ' 0VBHAT' — our internal 'X' (entrance cell) must be
    # exported as 'A' or the object is silently unvisitable in-game (found in playtest:
    # "ERROR Unrecognized char X in template mask", mines could not be flagged)
    for vo in vobjs:
        for row in vo["template"]["mask"]:
            assert set(row) <= set(" 0VBHAT"), f"invalid VCMI mask row {row!r}"
    assert any("A" in row for row in vobjs[0]["template"]["mask"]), \
        "the town's entrance cell must survive as a VCMI-visitable 'A'"
    wired = [pl for pl in header["players"].values()
             if isinstance(pl, dict) and pl.get("mainTown")]
    assert wired, "a player slot must be wired to the town"


def test_vmap_export_game_contracts():
    """Round-2 playtest contracts (v5.2): mask orientation matches the art (the sawmill
    entrance is ONE tile left of the anchor, not mirrored), export masks are V-padded to
    the sprite tile extent (VCMI truncates sprites outside the mask), guards fight
    (character=hostile) and towns start with a fort."""
    import glob
    import json
    import zipfile
    if not glob.glob(RANDOMMAPS_GLOB):
        pytest.skip("VCMI template .vmap not available")
    import ontology as ON
    import pp_gameplay as PG
    import pp_map as PM
    import zone_engine as ZE
    # orientation: internal footprint un-mirrored, export mask == the mask VCMI's own RMG
    # writes for the same sawmill sprite (ground truth from Maps/RandomMaps). mask_of is
    # windowed identically to vmap_mask_of (same V-padding) so a guard's approach tile always
    # lands on the tile VCMI actually reads as visitable; only the X/A entrance glyph differs.
    assert ON.mask_of("avmsawg0") == ["VVVVV", "VVVBB", "VBBXB"]
    assert ON.vmap_mask_of("avmsawg0") == ["VVVVV", "VVVBB", "VBBAB"]
    assert ON.vmap_mask_of("avcranx0") == ["VVVVVV", "VVVVVV", "VVVVVV",
                                           "VVBBBV", "VBBBBB", "VBBABB"]
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    # a placed zone carries the game-time options on the right purposes
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    objs, *_ = PG.place_zone(ts, zones, 1, "grass", seed=3, force_town=True)
    town = next(o for o in objs if o["purpose"] == "TOWN")
    START_BUILDINGS = {"allOf": ["core:fort", "core:tavern",
                                 "core:dwellingLvl1", "core:dwellingLvl2"]}
    assert town["options"]["buildings"] == START_BUILDINGS
    assert town["options"]["possibleSpells"] == PG.CORE_SPELLS
    guards = [o for o in objs if o["purpose"] == "GUARD"]
    assert all(o["options"]["character"] == "hostile" for o in guards)
    # random dwellings in a town zone are marked with the town's coordinates ...
    rdwell = [o for o in objs if str(o.get("type", "")).startswith("randomDwelling")]
    assert all(o["options"]["sameAsTown"] == [town["x"], town["y"], 0] for o in rdwell)
    # ... and they survive the .vmap round trip, with sprite-extent masks
    grid = [[2] * 30 for _ in range(24)]
    cells = ZE.tile_terrain(grid, 30, 24)
    out = os.path.join(os.path.dirname(PS.PP_DIR), "..", "out", "vmap",
                       "test_pp_contracts.vmap")
    p = PM.export_vmap([cells], objs, os.path.abspath(out), name="test")
    vobjs = json.loads(zipfile.ZipFile(p).read("objects.json").decode())
    vtown = next(vo for vo in vobjs if vo.get("type") in ("town", "randomTown"))
    assert vtown["options"]["buildings"] == START_BUILDINGS
    assert vtown["options"]["possibleSpells"] == PG.CORE_SPELLS
    assert len(vtown["template"]["mask"]) == 6, "town mask must span the full sprite"
    # the coordinate marker resolved to the town's minted instanceName
    vdwell = [vo for vo in vobjs if str(vo.get("type", "")).startswith("randomDwelling")]
    if rdwell:
        assert vdwell and all(vo["options"]["sameAsTown"] == vtown["instanceName"]
                              for vo in vdwell)
    vguards = [vo for vo in vobjs if str(vo.get("type", "")).startswith("randomMonster")
               or vo.get("type") == "monster"]
    assert vguards and all(vo["options"]["character"] == "hostile" for vo in vguards)
    for vo in vobjs:
        for row in vo["template"]["mask"]:
            assert set(row) <= set(" 0VBHAT"), f"invalid VCMI mask row {row!r}"
    saws = [vo for vo in vobjs if vo.get("subtype") == "sawmill"]
    assert saws and all(vo["template"]["mask"] == ["VVVVV", "VVVBB", "VBBAB"]
                        for vo in saws)


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
    if not glob.glob(RANDOMMAPS_GLOB):
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
    p = PM.export_vmap([cells], towns, os.path.abspath(out), name="test")
    PM.apply_playability(p, towns, teams=[0, 1])
    h = json.loads(zipfile.ZipFile(p).read("header.json").decode())
    playable = {pid: pl for pid, pl in h["players"].items()
                if isinstance(pl, dict) and pl.get("canPlay") == "PlayerOrAI"}
    assert len(playable) == 2, "exactly N playable slots"
    wired = sorted((pl["mainTown"]["x"], pl["mainTown"]["y"]) for pl in playable.values())
    assert wired == [(6, 6), (16, 16)], "each player wired to its designated town (-2 offset)"
    assert sorted(pl["team"] for pl in playable.values()) == [0, 1]
    assert "teams" not in h, "no repeated team id (FFA/singletons) -> no top-level grouping"
    # these test towns are CONCRETE, so the lobby must be locked to the authored faction
    # (a randomTown start would instead clear allowedFactions — free lobby pick)
    for pl in playable.values():
        af = pl.get("allowedFactions")
        assert af == {"anyOf": [f"core:{towns[0]['subtype']}"]}, \
            f"concrete start town must restrict allowedFactions, got {af}"
    for pid, pl in h["players"].items():
        if isinstance(pl, dict) and pid not in playable:
            assert pl.get("canPlay") == "false" and pl.get("mainTown") is None


def test_playability_overlay_alliance_grouping():
    """apply_playability: a real 2v2 alliance (repeated team ids) must populate the
    top-level header["teams"] grouping — this is what VCMI's map-select screen actually
    reads to show alliances; the per-player "team" int alone is not enough (bug reported
    2026-07-05: '2v2' teams weren't shown when the map was selected in VCMI)."""
    import glob
    import json
    import zipfile
    if not glob.glob(RANDOMMAPS_GLOB):
        pytest.skip("VCMI template .vmap not available")
    import ontology as ON
    import pp_map as PM
    import zone_engine as ZE
    grid = [[2] * 24 for _ in range(24)]
    cells = ZE.tile_terrain(grid, 24, 24)
    town = ON.gameplay_pool("grass", "TOWN")[0]

    def mk(x, y):
        return {"x": x, "y": y, "l": 0, "purpose": "TOWN", "type": town["type"],
                "subtype": town["subtype"], "animation": town["animation"],
                "mask": town["mask"],
                "template": {"animation": town["animation"], "mask": town["mask"]}}
    towns = [mk(8, 8), mk(18, 18), mk(8, 18), mk(18, 8)]
    out = os.path.join(os.path.dirname(PS.PP_DIR), "..", "out", "vmap",
                       "test_pp_play_2v2.vmap")
    p = PM.export_vmap([cells], towns, os.path.abspath(out), name="test")
    PM.apply_playability(p, towns, teams=[0, 0, 1, 1])
    h = json.loads(zipfile.ZipFile(p).read("header.json").decode())
    assert sorted(sorted(g) for g in h["teams"]) == [["blue", "green"], ["orange", "red"]]


def test_playability_overlay_random_town_shows_random_in_lobby():
    """A randomTown start must set randomFaction=true, or VCMI's PlayerInfo::defaultCastle()
    (isFactionRandom false + allowedFactions defaulting to ALL on an absent key) picks the
    first faction by id (Castle) instead of showing 'random' in the lobby — the bug reported
    2026-07-03: every player's town appeared fixed to Castle."""
    import glob
    import json
    import zipfile
    if not glob.glob(RANDOMMAPS_GLOB):
        pytest.skip("VCMI template .vmap not available")
    import pp_gameplay as PG
    import pp_map as PM
    import zone_engine as ZE
    rnd = PG.ON.identity_of(PG.RND_TOWN)
    grid = [[2] * 24 for _ in range(24)]
    cells = ZE.tile_terrain(grid, 24, 24)
    towns = [{"x": 8, "y": 8, "l": 0, "purpose": "TOWN", "type": rnd["type"],
              "subtype": rnd["subtype"], "animation": rnd["animation"], "mask": rnd["mask"],
              "template": {"animation": rnd["animation"], "mask": rnd["mask"]}}]
    out = os.path.join(os.path.dirname(PS.PP_DIR), "..", "out", "vmap",
                       "test_pp_play_random.vmap")
    p = PM.export_vmap([cells], towns, os.path.abspath(out), name="test")
    PM.apply_playability(p, towns, teams=[0])
    h = json.loads(zipfile.ZipFile(p).read("header.json").decode())
    pl = next(pl for pl in h["players"].values()
              if isinstance(pl, dict) and pl.get("canPlay") == "PlayerOrAI")
    assert pl.get("randomFaction") is True, "randomTown start must set randomFaction=true"
    assert "allowedFactions" not in pl, "no faction restriction on a free random pick"
    # the town OBJECT still carries options.owner (mainTown alone is not enough)
    vobjs = json.loads(zipfile.ZipFile(p).read("objects.json").decode())
    owners = [o["options"]["owner"] for o in vobjs
              if o.get("type") in ("town", "randomTown")
              and o.get("options", {}).get("owner") is not None]
    assert owners == ["blue"], "the single town must be owned by the sole (blue) player"


def test_zone_gate_bands_wide_and_protected():
    """Zone gates are corpus-wide BANDS of the contact front, and the protected web keeps
    the whole band vegetation-free — borders must never collapse to a 1-tile corridor."""
    import zone_field as ZF
    # two 12x10 zones side by side: the contact front is the full 10-tile column
    ts1 = {(x, y) for x in range(12) for y in range(10)}
    ts2 = {(x, y) for x in range(12, 24) for y in range(10)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (5.5, 4.5), "area": 120,
                 "terrain_type": 2},
             2: {"tiles_set": sorted(ts2), "centroid": (17.5, 4.5), "area": 120,
                 "terrain_type": 3}}
    bands = ZF._zone_gate_bands(ts1, zones, 1, open_frac=0.5)
    fronts = [(rep, band) for rep, band in bands if all(t[0] == 11 for t in band)]
    assert fronts, "zone 1 must have a band on its contact front with zone 2"
    rep, band = fronts[0]
    assert len(band) >= 5, f"open_frac=0.5 of a 10-tile front is 5 tiles, got {len(band)}"
    assert rep in band and all(t in ts1 for t in band)
    # a tiny open_frac still keeps the minimum width
    bands_min = ZF._zone_gate_bands(ts1, zones, 1, open_frac=0.01)
    _rep, band_min = [(r, b) for r, b in bands_min if all(t[0] == 11 for t in b)][0]
    assert len(band_min) >= 3, "bands never collapse below min_w"
    # determinism
    assert ZF._zone_gate_bands(ts1, zones, 1, open_frac=0.5) == bands


@needs_stats
def test_protected_web_covers_gate_bands():
    import pp_sample as PP
    import zone_field as ZF
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
def test_town_zone_gets_wood_and_ore_next_to_town():
    """A zone with a town ALWAYS holds a sawmill + ore pit, anchored near the town."""
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    for seed in (1, 4, 9):
        objs, *_ = PG.place_zone(ts, zones, 1, "grass", seed=seed, force_town=True)
        towns = [o for o in objs if o["purpose"] == "TOWN"]
        assert towns, f"seed {seed}: forced town missing"
        subs = {o["subtype"] for o in objs if o["purpose"] == "MINE"}
        assert {"sawmill", "orePit"} <= subs, f"seed {seed}: economy pair missing ({subs})"
        t = towns[0]
        for m in (o for o in objs if o["purpose"] == "MINE"
                  and o["subtype"] in ("sawmill", "orePit")):
            d = max(abs(m["x"] - t["x"]), abs(m["y"] - t["y"]))
            assert d <= 12, f"seed {seed}: {m['subtype']} is {d} tiles from the town"


@needs_stats
def test_mine_ledger_covers_basics_and_rations_gold():
    """The map-level ledger drives zones to cover all six basic resources and blocks gold
    mines until the map holds several towns."""
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(40) for y in range(30)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (19.5, 14.5), "area": len(ts),
                 "terrain_type": 2}}
    # gold is rationed to towns - 1 (a zone may roll a neutral town of its own, which
    # legitimately raises the quota — the INVARIANT is what must hold)
    for seed in range(1, 8):
        ledger = {"missing": set(PG.BASIC_MINE_RES), "towns": 1, "gold": 0}
        objs, *_ = PG.place_zone(ts, zones, 1, "grass", seed=seed, ledger=ledger)
        n_gold = sum(1 for o in objs
                     if o["purpose"] == "MINE" and o["subtype"] == "goldMine")
        assert n_gold == ledger["gold"] <= max(0, ledger["towns"] - 1), \
            f"seed {seed}: gold {n_gold} exceeds quota (towns={ledger['towns']})"
    # missing basics are drawn FIRST: a fresh ledger shrinks by every mine the zone placed
    ledger = {"missing": set(PG.BASIC_MINE_RES), "towns": 1, "gold": 0}
    objs, *_ = PG.place_zone(ts, zones, 1, "grass", seed=3, ledger=ledger)
    n_mines = sum(1 for o in objs if o["purpose"] == "MINE")
    assert len(ledger["missing"]) <= max(0, len(PG.BASIC_MINE_RES) - n_mines), \
        "every placed mine must come from the missing set while it is non-empty"


@needs_stats
def test_banks_placed_on_land_and_legal():
    """Creature banks (utopias, conservatories, crypts...) place on land like visitables:
    full footprint in-zone, approach standable, no extra approach guard."""
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(45) for y in range(40)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (22.0, 19.5), "area": len(ts),
                 "terrain_type": 2}}
    banks = []
    for seed in range(1, 12):
        objs, occupied, blocked, approaches = PG.place_zone(ts, zones, 1, "grass",
                                                            seed=seed)
        banks += [o for o in objs if o["purpose"] == "BANK"]
    assert banks, "a 1800-tile grass zone must produce banks across a dozen seeds"
    for b in banks:
        allc, blk, approach = PG._cells({"mask": b["template"]["mask"]}, b["x"], b["y"])
        assert approach is not None and all(c in ts for c in allc)


@needs_stats
def test_scatter_rewards_are_mostly_loot():
    """Unguarded reward scatter favours the fixed LOOT pool (treasure chests dominate the
    corpus mix) over random artifacts, and a real zone always yields some loot."""
    import pp_gameplay as PG
    import pp_pickup as PK
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    prot = {(x, 12) for x in range(30)} | {(15, y) for y in range(24)}
    rewards = []
    for seed in range(1, 10):
        objs = PK.place_pickups(ts, zones, 1, "grass", set(ts), prot, seed=seed)
        rewards += [o for o in objs if o["purpose"] == "REWARD_PICKUP"]
    assert rewards, "a 720-tile zone (>= LOOT_FLOOR_AREA) must yield reward pickups"
    # guarded-pocket rewards are deliberately tiered random artifacts (bug-report fix: those
    # pockets were underutilized); the LOOT-pool claim applies to the unguarded scatter only
    scatter_rewards = [o for o in rewards if not o.get("cache")]
    loot = [o for o in scatter_rewards if "random" not in str(o["type"]).lower()]
    assert any(o["type"] == "treasureChest" for o in loot), \
        "treasure chests must appear as unguarded loot"
    assert len(loot) >= len(scatter_rewards) * 0.5, \
        f"scatter must be mostly fixed loot, got {len(loot)}/{len(scatter_rewards)}"


@needs_stats
def test_mine_sprites_match_terrain():
    """Mine DEFs carry a baked-in terrain apron; placed mines must use variants the corpus
    actually uses on that terrain (no dirt-apron gold mine on grass)."""
    import json
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    st = json.load(open(PG.STATS_PATH))
    ts = {(x, y) for x in range(40) for y in range(30)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (19.5, 14.5), "area": len(ts),
                 "terrain_type": 2}}
    for terrain, tt in (("grass", 2), ("snow", 3)):
        zones[1]["terrain_type"] = tt
        mw = st[terrain]["anim_w"]["MINE"]
        for seed in range(1, 8):
            objs, *_ = PG.place_zone(ts, zones, 1, terrain, seed=seed)
            for m in (o for o in objs if o["purpose"] == "MINE"):
                w = mw.get(m["animation"].lower(), 0)
                assert w > 0, (f"{terrain}: mine variant {m['animation']} "
                               f"({m['subtype']}) never used on {terrain} in the corpus")


@needs_stats
def test_audit_variety_green():
    """Every corpus (purpose, animation) on land must be reachable through the generator
    (identity via the ontology, placement via a pool) — the acceptance check for corpus
    visitable variety."""
    import pp_gameplay as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    gaps = PG.audit_variety()
    assert gaps == [], f"variety gaps: {gaps}"


def test_macro_generate_deterministic_and_coarse():
    import macro_topo as MT
    g1 = MT.generate(48, 48, seed=1)
    g2 = MT.generate(48, 48, seed=1)
    assert g1 == g2, "macro terrain must be seed-deterministic"
    rep = MT.report(g1)
    # the §4.3 gate: the macro layer must NOT fragment (the markov failure mode)
    assert rep["big_share"] >= 0.7, rep


def test_despeckle_absorbs_tiny_zones():
    """Shape-aware sliver rule: a terrain patch (= future zone) survives despeckle only when
    it has >4 tiles or is a compact 2x2 square; narrow 4-tile shapes and anything smaller are
    absorbed into the dominant LAND neighbour (water/rock only when no land borders it)."""
    import zone_engine as ZE

    GRASS, DIRT, WATER = 2, 0, 8
    W, H = 24, 14
    ids = [[GRASS] * W for _ in range(H)]
    for x in range(3, 7):                 # 1x4 dirt line — narrow, must be absorbed
        ids[3][x] = DIRT
    for x, y in ((10, 5), (11, 5), (10, 6), (11, 6)):   # 2x2 dirt square — a fine zone, kept
        ids[y][x] = DIRT
    ids[9][3] = ids[9][4] = ids[10][3] = DIRT           # 3-tile L — absorbed
    # 4-tile dirt line hugging a water edge: water borders it on more sides than grass does,
    # but land preference must still repaint it GRASS, not water
    for x in range(16, 22):
        for y in range(2, 5):
            ids[y][x] = WATER
    for x in range(17, 21):
        ids[5][x] = DIRT
    # 2x2 grass island fully inside water — compact, must survive as its own zone
    for x in range(16, 22):
        for y in range(8, 13):
            ids[y][x] = WATER
    for x, y in ((18, 10), (19, 10), (18, 11), (19, 11)):
        ids[y][x] = GRASS

    out = ZE._despeckle_ids(ids, W, H)
    assert out == ZE._despeckle_ids(ids, W, H), "despeckle must be deterministic"

    assert all(out[3][x] == GRASS for x in range(3, 7)), "1x4 line absorbed into grass"
    assert all(out[y][x] == DIRT for x, y in ((10, 5), (11, 5), (10, 6), (11, 6))), \
        "2x2 square kept"
    assert out[9][3] == out[9][4] == out[10][3] == GRASS, "3-tile patch absorbed"
    assert all(out[5][x] == GRASS for x in range(17, 21)), \
        "water-hugging sliver becomes LAND (grass), never water"
    assert all(out[y][x] == GRASS for x, y in ((18, 10), (19, 10), (18, 11), (19, 11))), \
        "2x2 island in water survives"

    # no surviving land patch violates the rule
    seen = set()
    for y in range(H):
        for x in range(W):
            if (x, y) in seen or out[y][x] >= WATER:
                continue
            t, stack, tiles = out[y][x], [(x, y)], [(x, y)]
            seen.add((x, y))
            while stack:
                a, b = stack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = a + dx, b + dy
                    if (0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen
                            and out[ny][nx] == t):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
                        tiles.append((nx, ny))
            assert ZE._keep_patch(tiles), f"sliver patch survived: {sorted(tiles)}"


def test_plan_entrances_aligned_and_few():
    """Entrance plan: 1 crossing for a short front, 2 for a long one, both sides' bands
    aligned (4-adjacent across the border), <=ENTRANCE_W tiles per side, deterministic."""
    import zone_field as ZF

    # short front: two 12x10 zones -> exactly ONE entrance for the pair
    ts1 = {(x, y) for x in range(12) for y in range(10)}
    ts2 = {(x, y) for x in range(12, 24) for y in range(10)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (5.5, 4.5), "area": 120,
                 "terrain_type": 2},
             2: {"tiles_set": sorted(ts2), "centroid": (17.5, 4.5), "area": 120,
                 "terrain_type": 3}}
    plan = ZF.plan_entrances(zones)
    assert len(plan[1]) == 1 and len(plan[2]) == 1, "10-tile front gets a single entrance"
    rep1, band1, other1 = plan[1][0]
    rep2, band2, other2 = plan[2][0]
    assert other1 == 2 and other2 == 1
    assert rep1 in band1 and band1 <= ts1 and len(band1) <= ZF.ENTRANCE_W
    assert rep2 in band2 and band2 <= ts2 and len(band2) <= ZF.ENTRANCE_W
    assert abs(rep1[0] - rep2[0]) + abs(rep1[1] - rep2[1]) == 1, \
        "the two sides' reps must be 4-adjacent (aligned crossing)"
    assert plan == ZF.plan_entrances(zones), "planner must be deterministic"

    # long front: 30-tile contact column -> two entrances, far apart
    ts3 = {(x, y) for x in range(12) for y in range(30)}
    ts4 = {(x, y) for x in range(12, 24) for y in range(30)}
    zl = {1: {"tiles_set": sorted(ts3), "centroid": (5.5, 14.5), "area": 360,
              "terrain_type": 2},
          2: {"tiles_set": sorted(ts4), "centroid": (17.5, 14.5), "area": 360,
              "terrain_type": 3}}
    plan2 = ZF.plan_entrances(zl)
    assert len(plan2[1]) == ZF.MAX_ENTRANCES, "30-tile front earns a second entrance"
    (ra, _, _), (rb, _, _) = plan2[1]
    assert max(abs(ra[0] - rb[0]), abs(ra[1] - rb[1])) >= ZF.MIN_ENTRANCE_SEP, \
        "the two entrances of one pair must not crowd each other"


@needs_stats
def test_border_bias_densifies_front():
    """Zone isolation: with BOTH zones sampling under the `border=` bias, the only aligned
    open crossings left between them are the planned entrance band — each single side is
    only a partial ridge (Geyer saturation caps clumping), but the seal is 2-thick."""
    import pp_sample as PP
    import zone_field as ZF

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


@needs_stats
def test_entrance_guard_single_side():
    """With an entrance plan, only the LOWER zid of a pair emits the crossing guard, and it
    sits on the planned rep/band (a genuine chokepoint), not on a random pocket mouth."""
    import pp_gameplay as PG
    import zone_field as ZF

    ts1 = {(x, y) for x in range(14) for y in range(12)}
    ts2 = {(x, y) for x in range(14, 28) for y in range(12)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (6.5, 5.5), "area": 168,
                 "terrain_type": 2},
             2: {"tiles_set": sorted(ts2), "centroid": (20.5, 5.5), "area": 168,
                 "terrain_type": 2}}
    plan = ZF.plan_entrances(zones)
    crossing1 = {t for r, b, _o in plan[1] for t in b | {r}}
    crossing2 = {t for r, b, _o in plan[2] for t in b | {r}}

    n_guarded = 0
    for seed in range(1, 7):
        objs1, *_ = PG.place_zone(ts1, zones, 1, "grass", seed=seed, entrances=plan[1])
        objs2, *_ = PG.place_zone(ts2, zones, 2, "grass", seed=seed, entrances=plan[2])
        g1 = [o for o in objs1 if o["purpose"] == "GUARD" and (o["x"], o["y"]) in crossing1]
        g2 = [o for o in objs2 if o["purpose"] == "GUARD" and (o["x"], o["y"]) in crossing2]
        assert len(g1) <= 1, "at most one guard per planned entrance"
        assert not g2, "zone 2 (higher zid) must never emit the pair's crossing guard"
        n_guarded += len(g1)
    assert n_guarded >= 3, \
        f"ENTRANCE_GUARD_PROB=0.85 should guard most seeds, got {n_guarded}/6"


@needs_stats
def test_portal_reward_zone():
    """A rock-enclosed zone becomes a SPECIAL REWARD zone: a same-subtype two-way monolith
    pair bridges it (far end inside, near end in the reachable host zone with a hostile
    guard adjacent), cache-tagged loot fills it, traverse counts it reachable, and
    fill_open_islands no longer buries it in decoration."""
    import pp_map as PM
    import traverse as TR
    import mapeval as ME

    S = 40
    GRASS, ROCK = 2, 9
    grid = [[GRASS] * S for _ in range(S)]
    inner = {(x, y) for x in range(31, 37) for y in range(31, 37)}   # 36-tile enclave
    for y in range(28, 40):
        for x in range(28, 40):
            if (x, y) not in inner:
                grid[y][x] = ROCK
    ts1 = {(x, y) for x in range(S) for y in range(S) if grid[y][x] == GRASS} - inner
    zones = {1: {"tiles_set": sorted(ts1), "area": len(ts1), "terrain_type": GRASS,
                 "centroid": (sum(x for x, _ in ts1) / len(ts1),
                              sum(y for _, y in ts1) / len(ts1))},
             2: {"tiles_set": sorted(inner), "area": len(inner), "terrain_type": GRASS,
                 "centroid": (33.5, 33.5)}}

    def mk(typ, x, y, purpose, mask=("A",)):
        return {"type": typ, "subtype": "s", "animation": "X", "mask": list(mask),
                "x": x, "y": y, "l": 0, "purpose": purpose,
                "template": {"animation": "X", "mask": list(mask)}}

    def run():
        objs = {0: [mk("town", 5, 5, "TOWN"), mk("mine", 31, 31, "MINE")]}
        targets = {0: [(5, 6)]}
        n = PM.rescue_unreachable_zones(
            S, {0: grid}, {0: zones}, objs, targets, {0: []},
            start=(0, (5, 5)), gate_xy=set(), seed=3)
        return n, objs[0], targets[0]

    n, objs, targets = run()
    assert n == 1, "the enclave must be rescued by exactly one portal pair"
    mono = [o for o in objs if o["type"] == "monolithTwoWay"]
    assert len(mono) == 2 and mono[0]["subtype"] == mono[1]["subtype"], \
        "a same-subtype two-way pair"
    far = [o for o in mono if (o["x"], o["y"]) in inner]
    near = [o for o in mono if (o["x"], o["y"]) in ts1]
    assert len(far) == 1 and len(near) == 1, "one end inside, one end in the host zone"
    nx, ny = near[0]["x"], near[0]["y"]
    guards = [o for o in objs if o.get("purpose") == "GUARD"
              and max(abs(o["x"] - nx), abs(o["y"] - ny)) == 1]
    assert guards and guards[0]["options"] == {"character": "hostile"}, \
        "a hostile guard must sit adjacent to the reachable-side end"
    loot = [o for o in objs if o.get("cache")]
    assert len(loot) >= 6 and all((o["x"], o["y"]) in inner for o in loot), \
        "the enclave holds a dense cache-tagged hoard"
    assert set(targets) & {(o["x"], o["y"]) for o in loot}, "rewards are named G2 targets"

    # determinism
    n2, objs2, _ = run()
    assert n2 == n and objs2 == objs

    # traverse: the monolith network makes the enclave (and its mine) reachable
    cell = {"view": 0, "rt": 0, "rd": 0, "ot": 0, "od": 0, "m": 0}
    fm = {"name": "synthetic", "width": S, "height": S, "twoLevel": False, "players": 1,
          "terrain": [[[dict(cell, t=grid[y][x]) for x in range(S)] for y in range(S)]],
          "objects": objs}
    rep = TR.traverse(fm)
    assert rep["unreachable_mines"] == [], "the enclosed mine must be reachable via portal"
    assert ME.relational_complete(fm) is True

    # fill_open_islands: the enclave now carries targets -> no decoration fill
    _objs3, _nrec, nfill = PM.fill_open_islands(S, grid, list(objs), list(targets), seed=3)
    assert nfill == 0, f"rescued zone must not be decoration-filled, filled {nfill} tiles"


def test_seal_zone_borders_closes_or_guards():
    """Every cross-zone 8-adjacent open crossing outside the planned entrance bands is
    either SEALED with a blocking decoration or contested by a back-path GUARD's zone of
    control — an unguardable-and-unsealable free crossing must not survive."""
    import pp_map as PM
    import zone_field as ZF

    S, GRASS = 20, 2
    grid = [[GRASS] * S for _ in range(S)]
    ts1 = {(x, y) for x in range(10) for y in range(S)}
    ts2 = {(x, y) for x in range(10, S) for y in range(S)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (4.5, 9.5), "area": len(ts1),
                 "terrain_type": GRASS},
             2: {"tiles_set": sorted(ts2), "centroid": (14.5, 9.5), "area": len(ts2),
                 "terrain_type": GRASS}}
    plan = ZF.plan_entrances(zones)
    bands = set()
    for ents in plan.values():
        for _r, b, _o in ents:
            bands |= set(b)
    # two facing unsealable tiles (a protected-web crossing) must earn a guard, not a seal
    web_pair = {(9, 2), (10, 2)}
    avoid = bands | web_pair

    args = (S, S, grid, zones, plan, [], avoid, set(), 3, 0)
    new_objs, sealed, guard_tiles, n_open = PM.seal_zone_borders(*args)
    assert PM.seal_zone_borders(*args)[:2] == (new_objs, sealed), "deterministic"

    assert sealed and not (sealed & avoid), "seals never land on protected tiles"
    assert guard_tiles & web_pair, "the unsealable web crossing gets a back-path guard"
    assert n_open == 0
    guards = [o for o in new_objs if o.get("purpose") == "GUARD"]
    assert all(o.get("seal") and o["options"] == {"character": "hostile"} for o in guards)

    open_all = (ts1 | ts2) - sealed

    def zoc(t):
        return any(max(abs(g[0] - t[0]), abs(g[1] - t[1])) <= 1 for g in guard_tiles)

    for t in sorted(open_all):
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            n = (t[0] + dx, t[1] + dy)
            if n not in open_all:
                continue
            if (t in ts1) == (n in ts1):
                continue
            assert t in bands or n in bands or zoc(t) or zoc(n), \
                f"free unguarded crossing survived at {t}->{n}"
