"""Reliability tests for renderers.vmap (VmapRenderer: .vmap export + playability overlay)."""
import glob
import json
import os
import zipfile

import pytest

from vcmi_mapgen.kit import paths as vcmi_paths
from vcmi_mapgen.pipeline import MapState
from vcmi_mapgen.renderers.vmap import VmapRenderer, _parse_teams

RANDOMMAPS_GLOB = os.path.join(vcmi_paths.vcmi_home(), "Maps", "RandomMaps", "*.vmap")
needs_vcmi = pytest.mark.skipif(not glob.glob(RANDOMMAPS_GLOB),
                                reason="VCMI template .vmap not available")


def test_parse_teams():
    assert _parse_teams("ffa", 3) == [0, 1, 2]
    assert _parse_teams("2v2", 4) == [0, 0, 1, 1]
    assert _parse_teams("1v3", 4) == [0, 1, 1, 1]
    assert _parse_teams("0,0,1,1", 4) == [0, 0, 1, 1]
    with pytest.raises(ValueError):
        _parse_teams("2v2", 3)


@needs_vcmi
def test_vmap_export_roundtrip(tmp_path):
    """VmapRenderer writes an editor-shaped .vmap: reads back, visitables carry
    visitableFrom, and a playable slot is wired to the town."""
    from vcmi_mapgen import ontology as ON
    from vcmi_mapgen.kit import tiling as ZE
    grid = [[2] * 16 for _ in range(16)]
    cells = ZE.tile_terrain(grid, 16, 16)
    town = ON.gameplay_pool("grass", "TOWN")[0]
    objs = [{"x": 8, "y": 8, "l": 0, "purpose": "TOWN", "type": town["type"],
             "subtype": town["subtype"], "animation": town["animation"],
             "mask": town["mask"],
             "template": {"animation": town["animation"], "mask": town["mask"]}}]
    state = MapState(cells={0: cells}, objs=objs)
    p = VmapRenderer(out_dir=str(tmp_path)).render(state, "test_pp_export.vmap", name="test")
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


@needs_vcmi
def test_vmap_export_game_contracts(tmp_path):
    """Round-2 playtest contracts (v5.2): mask orientation matches the art (the sawmill
    entrance is ONE tile left of the anchor, not mirrored), export masks are V-padded to
    the sprite tile extent (VCMI truncates sprites outside the mask), guards fight
    (character=hostile) and towns start with a fort."""
    from vcmi_mapgen import ontology as ON
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.kit import tiling as ZE
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
    state = MapState(cells={0: cells}, objs=objs)
    p = VmapRenderer(out_dir=str(tmp_path)).render(state, "test_pp_contracts.vmap", name="test")
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


@needs_vcmi
def test_playability_overlay(tmp_path):
    """VmapRenderer's playability overlay: exactly N playable slots wired to their towns,
    team matrix set, victory = defeat all (standardWin)."""
    from vcmi_mapgen import ontology as ON
    from vcmi_mapgen.kit import tiling as ZE
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
    state = MapState(cells={0: cells}, objs=towns, player_towns=towns)
    p = VmapRenderer(out_dir=str(tmp_path)).render(state, "test_pp_play.vmap", name="test",
                                                    teams_spec="ffa")
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


@needs_vcmi
def test_playability_overlay_alliance_grouping(tmp_path):
    """A real 2v2 alliance (repeated team ids) must populate the top-level
    header["teams"] grouping — this is what VCMI's map-select screen actually reads to
    show alliances; the per-player "team" int alone is not enough (bug reported
    2026-07-05: '2v2' teams weren't shown when the map was selected in VCMI)."""
    from vcmi_mapgen import ontology as ON
    from vcmi_mapgen.kit import tiling as ZE
    grid = [[2] * 24 for _ in range(24)]
    cells = ZE.tile_terrain(grid, 24, 24)
    town = ON.gameplay_pool("grass", "TOWN")[0]

    def mk(x, y):
        return {"x": x, "y": y, "l": 0, "purpose": "TOWN", "type": town["type"],
                "subtype": town["subtype"], "animation": town["animation"],
                "mask": town["mask"],
                "template": {"animation": town["animation"], "mask": town["mask"]}}
    towns = [mk(8, 8), mk(18, 18), mk(8, 18), mk(18, 8)]
    state = MapState(cells={0: cells}, objs=towns, player_towns=towns)
    p = VmapRenderer(out_dir=str(tmp_path)).render(state, "test_pp_play_2v2.vmap", name="test",
                                                    teams_spec="2v2")
    h = json.loads(zipfile.ZipFile(p).read("header.json").decode())
    assert sorted(sorted(g) for g in h["teams"]) == [["blue", "green"], ["orange", "red"]]


@needs_vcmi
def test_playability_overlay_random_town_shows_random_in_lobby(tmp_path):
    """A randomTown start must set randomFaction=true, or VCMI's PlayerInfo::defaultCastle()
    (isFactionRandom false + allowedFactions defaulting to ALL on an absent key) picks the
    first faction by id (Castle) instead of showing 'random' in the lobby — the bug reported
    2026-07-03: every player's town appeared fixed to Castle."""
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.kit import tiling as ZE
    rnd = PG.ON.identity_of(PG.RND_TOWN)
    grid = [[2] * 24 for _ in range(24)]
    cells = ZE.tile_terrain(grid, 24, 24)
    towns = [{"x": 8, "y": 8, "l": 0, "purpose": "TOWN", "type": rnd["type"],
              "subtype": rnd["subtype"], "animation": rnd["animation"], "mask": rnd["mask"],
              "template": {"animation": rnd["animation"], "mask": rnd["mask"]}}]
    state = MapState(cells={0: cells}, objs=towns, player_towns=towns)
    p = VmapRenderer(out_dir=str(tmp_path)).render(state, "test_pp_play_random.vmap",
                                                    name="test", teams_spec="ffa")
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
