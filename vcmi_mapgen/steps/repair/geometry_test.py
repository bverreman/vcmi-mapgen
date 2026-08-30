"""Reliability tests for steps.repair.geometry (G2 repair, island fill, portal rescue)."""


def test_g2_repair_carves_vegetation_only():
    """A vegetation wall between two pickups gets carved; gameplay walls do not."""
    from vcmi_mapgen.steps.repair import geometry as GEO
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
    objs, removed = GEO.g2_repair(size, grid, veg_wall + picks, [(2, 5), (10, 5)])
    assert removed >= 1, "the veg wall must be carved"
    _, removed2 = GEO.g2_repair(size, grid, objs, [(2, 5), (10, 5)])
    assert removed2 == 0, "after repair the map must be G2-clean"
    # same wall as GAMEPLAY (purpose set) must NOT be carved
    hard_wall = [dict(o, purpose="DWELLING") for o in veg_wall]
    objs3, removed3 = GEO.g2_repair(size, grid, hard_wall + picks, [(2, 5), (10, 5)])
    assert removed3 == 0 and len(objs3) == len(hard_wall) + len(picks)


def test_portal_reward_zone():
    """A rock-enclosed zone becomes a SPECIAL REWARD zone: a same-subtype two-way monolith
    pair bridges it (far end inside, near end in the reachable host zone with a hostile
    guard adjacent), cache-tagged loot fills it, traverse counts it reachable, and
    fill_open_islands no longer buries it in decoration."""
    from vcmi_mapgen.steps.repair import geometry as GEO
    from vcmi_mapgen import traverse as TR
    from vcmi_mapgen import mapeval as ME

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
        n = GEO.rescue_unreachable_zones(
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
    _objs3, _nrec, nfill = GEO.fill_open_islands(S, grid, list(objs), list(targets), seed=3)
    assert nfill == 0, f"rescued zone must not be decoration-filled, filled {nfill} tiles"
