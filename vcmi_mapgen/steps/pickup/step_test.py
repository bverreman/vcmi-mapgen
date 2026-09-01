"""Reliability tests for steps.pickup.step (PickupStep orchestration)."""
import contextlib
import io


def _run_through_pickup(seed, size=48, players=2, subterrain=True):
    from vcmi_mapgen.pipeline import PlacementWorkspace, VcmiMapGenPipeline
    from vcmi_mapgen.steps import (GameplayStep, GateStep, PickupStep, SegmentStep,
                                    TerrainGenStep, TileStep, VegetationStep)

    workspace = PlacementWorkspace()
    pipeline = VcmiMapGenPipeline(ontology=None)
    pipeline.add_step(TerrainGenStep(size=size, seed=seed, water_mode="normal",
                                     subterrain=subterrain))
    pipeline.add_step(TileStep())
    pipeline.add_step(SegmentStep())
    if subterrain:
        pipeline.add_step(GateStep(seed=seed))
    pipeline.add_step(GameplayStep(seed=seed, players=players, workspace=workspace))
    pipeline.add_step(VegetationStep(seed=seed, workspace=workspace))
    pipeline.add_step(PickupStep(seed=seed, workspace=workspace))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        state = pipeline.run()
    return state


def test_loot_zone_sealing_never_drops_a_subterranean_gate():
    """seed=7/size=48/subterrain places a Subterranean Gate pair whose (17, 14) tile falls
    inside a zone PickupStep later seals as a loot zone. Legacy build() only appended
    gate_objs to the object list AFTER its equivalent of Vegetation+Pickup+Repair had
    already run for that level, so the gate was physically absent during loot-zone
    sealing and could never be swept by its "clear everything at this (x, y)" pass. This
    pipeline merges gate objects in earlier (GameplayStep, so downstream forbid/occupied
    sets see them) — exposing them to that sweep whenever a gate shares its (x, y) with a
    loot zone. Regression for the fix that shields gate_objs (by identity) around the
    sealing pass in PickupStep.run()."""
    state = _run_through_pickup(seed=7)
    gates_by_level = {0: [], 1: []}
    for o in state.objs:
        if o.get("type") == "subterraneanGate":
            gates_by_level[o.get("l", 0)].append((o["x"], o["y"]))
    assert gates_by_level[0], "fixture assumption broke: expected >= 1 gate pair on this seed"
    assert sorted(gates_by_level[0]) == sorted(gates_by_level[1]), (
        "a Subterranean Gate pair must exist on BOTH levels at the SAME (x, y) — "
        f"level 0 has {sorted(gates_by_level[0])}, level 1 has {sorted(gates_by_level[1])}"
    )
