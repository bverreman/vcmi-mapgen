"""Reliability tests for steps.gameplay.step (GameplayStep orchestration)."""
import contextlib
import io


def _run_gameplay(seed, size=48, players=2, subterrain=True):
    from vcmi_mapgen.pipeline import PlacementWorkspace, VcmiMapGenPipeline
    from vcmi_mapgen.steps import GameplayStep, GateStep, SegmentStep, TerrainGenStep, TileStep

    workspace = PlacementWorkspace()
    pipeline = VcmiMapGenPipeline(ontology=None)
    pipeline.add_step(TerrainGenStep(size=size, seed=seed, water_mode="normal",
                                     subterrain=subterrain))
    pipeline.add_step(TileStep())
    pipeline.add_step(SegmentStep())
    if subterrain:
        pipeline.add_step(GateStep(seed=seed))
    pipeline.add_step(GameplayStep(seed=seed, players=players, workspace=workspace))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        state = pipeline.run()
    return state


def test_player_towns_top_up_from_spare_when_a_forced_placement_fails():
    """seed=1/size=48/subterrain tries to force a town into player zone (1, 0) (level 1)
    and fails (no legal anchor — logged as a WARNING), leaving only 1 of 2 requested
    player towns resolved from town_of_zone. Legacy build() topped the count back up to
    `players` from any other neutral TOWN object already on the map; GameplayStep.run()
    dropped that fallback when the Pass-2 loot/vegetation logic was split out into its own
    steps, silently handing player 2 no start town at all. Regression for that fix."""
    state = _run_gameplay(seed=1)
    town_objs = [o for o in state.objs if o.get("purpose") == "TOWN"]
    assert len(town_objs) == 2, (
        "fixture assumption broke: expected exactly 2 TOWN objects on this seed/size "
        f"(one forced placement failing, one spare to top up from), got {len(town_objs)}"
    )
    assert len(state.player_towns) == 2, (
        f"only {len(state.player_towns)}/2 players got a start town — the spare-town "
        "top-up fallback isn't engaging"
    )
    # every returned town must be a real placed TOWN object, not a duplicate/placeholder
    town_positions = {(o["x"], o["y"], o.get("l", 0)) for o in town_objs}
    for t in state.player_towns:
        assert (t["x"], t["y"], t.get("l", 0)) in town_positions


def test_player_towns_never_exceeds_players_requested():
    """The top-up fallback must still cap at `players` — it must not hand out every spare
    neutral town on the map to a request for fewer players."""
    state = _run_gameplay(seed=1, players=1)
    assert len(state.player_towns) <= 1
