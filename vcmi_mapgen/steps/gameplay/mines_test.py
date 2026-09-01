"""Reliability tests for steps.gameplay.mines (L3 gameplay placement + corpus stats)."""
import os

import pytest

from vcmi_mapgen.steps.vegetation import stats as PS

HAVE_STATS = os.path.exists(os.path.join(PS.PP_DIR, "veg_grass.json"))
needs_stats = pytest.mark.skipif(not HAVE_STATS, reason="data/pp stats not mined")


@needs_stats
def test_gameplay_layer_legal_and_deterministic():
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.steps.gate.gates import GAP, _cells
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
        allc, blk, approach = _cells({"mask": o["template"]["mask"]}, o["x"], o["y"])
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
        allc, _b, _a = _cells({"mask": o["template"]["mask"]}, o["x"], o["y"])
        per_obj.append(allc)
    for i in range(len(per_obj)):
        for j in range(i + 1, len(per_obj)):
            d = min(max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                    for a in per_obj[i] for b in per_obj[j])
            assert d > GAP, f"objects {i},{j} too close (cheb {d})"


def test_select_player_zones_far_apart():
    """Player zones must be big AND mutually far apart — never all clustered together."""
    from vcmi_mapgen.steps.gameplay import mines as MN

    def zone(zid, cx, cy, area):
        return {"tiles_set": [], "centroid": (cx, cy), "area": area, "terrain_type": 2}
    zones = {0: zone(0, 36, 36, 500),                 # big centre
             1: zone(1, 4, 4, 220), 2: zone(2, 68, 4, 200),
             3: zone(3, 4, 68, 200), 4: zone(4, 68, 68, 220),
             5: zone(5, 40, 40, 300),                 # big but right NEXT to the centre
             6: zone(6, 30, 30, 40)}                  # too small: never a start
    zones_by_level = {0: zones}
    picks = MN.select_player_zones(zones_by_level, 2)
    assert picks[0] == (0, 0), "first pick is the largest zone"
    assert picks[1][1] in (1, 2, 3, 4), "second pick is a far corner, not the adjacent zone 5"
    picks4 = MN.select_player_zones(zones_by_level, 4)
    zids4 = [zid for _l, zid in picks4]
    assert 6 not in zids4 and 5 not in zids4, "small/adjacent zones lose to far corners"
    cents = [zones[zid]["centroid"] for _l, zid in picks4]
    dmin = min((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
               for i, a in enumerate(cents) for b in cents[i + 1:])
    assert dmin >= 32 ** 2, "chosen starts keep real distance between them"
    assert MN.select_player_zones(zones_by_level, 2) == picks, "selection is deterministic"


@needs_stats
def test_forced_town_sits_on_zone_centroid():
    """A designated player zone gets its town ON the centroid (footprint-centered)."""
    from vcmi_mapgen.steps.gameplay import mines as PG
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
def test_town_zone_gets_wood_and_ore_next_to_town():
    """A zone with a town ALWAYS holds a sawmill + ore pit, anchored near the town."""
    from vcmi_mapgen.steps.gameplay import mines as PG
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
    from vcmi_mapgen.steps.gameplay import mines as PG
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
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.steps.gate.gates import _cells
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
        allc, blk, approach = _cells({"mask": b["template"]["mask"]}, b["x"], b["y"])
        assert approach is not None and all(c in ts for c in allc)


@needs_stats
def test_mine_sprites_match_terrain():
    """Mine DEFs carry a baked-in terrain apron; placed mines must use variants the corpus
    actually uses on that terrain (no dirt-apron gold mine on grass)."""
    import json
    from vcmi_mapgen.steps.gameplay import mines as PG
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
    from vcmi_mapgen.steps.gameplay import mines as PG
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    gaps = PG.audit_variety()
    assert gaps == [], f"variety gaps: {gaps}"


@needs_stats
def test_entrance_guard_single_side():
    """With an entrance plan, only the LOWER zid of a pair emits the crossing guard, and it
    sits on the planned rep/band (a genuine chokepoint), not on a random pocket mouth."""
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.kit.topology import plan_entrances

    ts1 = {(x, y) for x in range(14) for y in range(12)}
    ts2 = {(x, y) for x in range(14, 28) for y in range(12)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (6.5, 5.5), "area": 168,
                 "terrain_type": 2},
             2: {"tiles_set": sorted(ts2), "centroid": (20.5, 5.5), "area": 168,
                 "terrain_type": 2}}
    plan = plan_entrances(zones)
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
