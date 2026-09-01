"""Reliability tests for steps.repair.caches (pocket caches, seer-hut quests, place_pickups)."""
import os

import pytest

from vcmi_mapgen.steps.vegetation import stats as PS

HAVE_STATS = os.path.exists(os.path.join(PS.PP_DIR, "veg_grass.json"))
needs_stats = pytest.mark.skipif(not HAVE_STATS, reason="data/pp stats not mined")


@needs_stats
def test_pickup_layer_legal_and_deterministic():
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.steps.gameplay.water import _legal
    from vcmi_mapgen.steps.repair import caches as CA
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    # synthetic open field with a sealed-off pocket-ish structure: a web cross + nooks
    prot = {(x, 12) for x in range(30)} | {(15, y) for y in range(24)}
    open_set = set(ts)
    o1 = CA.place_pickups(ts, zones, 1, "grass", open_set, prot, seed=6)
    o2 = CA.place_pickups(ts, zones, 1, "grass", open_set, prot, seed=6)
    assert o1 == o2, "pickup layer must be seed-deterministic"
    assert o1, "a 720-tile grass zone should hold pickups"
    from vcmi_mapgen.kit import objects as OR
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
        cells = _legal({"mask": o["template"]["mask"]}, o["x"], o["y"], open_set, set())
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
    from vcmi_mapgen.steps.repair import caches as CA
    from vcmi_mapgen import zone_field as ZF

    def best_mouths(reach, pocket_tiles):
        """canonical (deduped, ranked) mouth candidates whose pocket covers the nook"""
        raw = {m: c for m, c in ZF.find_pockets(reach).items()
               if set(pocket_tiles) <= set(c)}
        return [cands[0] for cands in CA._dedupe_pockets(raw, reach)]

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


@needs_stats
def test_scatter_rewards_are_mostly_loot():
    """Unguarded reward scatter favours the fixed LOOT pool (treasure chests dominate the
    corpus mix) over random artifacts, and a real zone always yields some loot."""
    from vcmi_mapgen.steps.gameplay import mines as PG
    from vcmi_mapgen.steps.repair import caches as CA
    if not os.path.exists(PG.STATS_PATH):
        pytest.skip("gameplay stats not mined")
    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    prot = {(x, 12) for x in range(30)} | {(15, y) for y in range(24)}
    rewards = []
    for seed in range(1, 10):
        objs = CA.place_pickups(ts, zones, 1, "grass", set(ts), prot, seed=seed)
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
