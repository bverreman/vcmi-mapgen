"""Reliability tests for steps.repair.border_seal (residual border-leak seal)."""


def test_seal_zone_borders_closes_or_guards():
    """Every cross-zone 8-adjacent open crossing outside the planned entrance bands is
    either SEALED with a blocking decoration or contested by a back-path GUARD's zone of
    control — an unguardable-and-unsealable free crossing must not survive."""
    from vcmi_mapgen.steps.repair import border_seal as BS
    from vcmi_mapgen.kit.topology import plan_entrances

    S, GRASS = 20, 2
    grid = [[GRASS] * S for _ in range(S)]
    ts1 = {(x, y) for x in range(10) for y in range(S)}
    ts2 = {(x, y) for x in range(10, S) for y in range(S)}
    zones = {1: {"tiles_set": sorted(ts1), "centroid": (4.5, 9.5), "area": len(ts1),
                 "terrain_type": GRASS},
             2: {"tiles_set": sorted(ts2), "centroid": (14.5, 9.5), "area": len(ts2),
                 "terrain_type": GRASS}}
    plan = plan_entrances(zones)
    bands = set()
    for ents in plan.values():
        for _r, b, _o in ents:
            bands |= set(b)
    # two facing unsealable tiles (a protected-web crossing) must earn a guard, not a seal
    web_pair = {(9, 2), (10, 2)}
    avoid = bands | web_pair

    args = (S, S, grid, zones, plan, [], avoid, set(), 3, 0)
    new_objs, sealed, guard_tiles, n_open = BS.seal_zone_borders(*args)
    assert BS.seal_zone_borders(*args)[:2] == (new_objs, sealed), "deterministic"

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
