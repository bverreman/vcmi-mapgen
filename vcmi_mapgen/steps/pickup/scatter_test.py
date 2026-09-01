"""Reliability tests for steps.pickup.scatter (unguarded L4a resource/artifact scatter)."""


def test_place_scatter_handles_a_zone_with_no_reachable_open_tile():
    """A zone whose whole tile set is already occupied by gameplay (open_set empty) still
    has a nonzero resource-pile quota drawn from its raw area — scatter()'s candidate list
    (built from `reach`, derived from `open_set`) then ends up empty, and
    `rng.choices(cands, weights=weights, k=...)` raises IndexError on an empty
    cum_weights. Both the legacy and current pipeline crashed identically here (a real,
    pre-existing, seed/refactor-independent bug); regression for the fix that returns
    early when there are no candidates instead of calling rng.choices()."""
    from vcmi_mapgen.steps.pickup import scatter as SC

    ts = {(x, y) for x in range(30) for y in range(24)}
    zones = {1: {"tiles_set": sorted(ts), "centroid": (14.5, 11.5), "area": len(ts),
                 "terrain_type": 2}}
    objs, used, reach = SC.place_scatter(ts, zones, 1, "grass", open_set=set(), prot=set(),
                                         seed=3, bounds=(30, 24))
    assert objs == []
    assert used == set()
    assert reach == set()
