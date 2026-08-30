"""GateStep — place Subterranean Gate pairs between surface and underground."""
from __future__ import annotations

from vcmi_mapgen.pipeline import MapState, PipelineStep
from vcmi_mapgen import zone_engine as ZE
from vcmi_mapgen.steps.gate import gates as PG

MIN_AREA = 25  # matches GameplayStep's own zone floor — a gate must land on a tile a
#                zone's own gameplay pass would actually consider (pipeline-refactor-v2-
#                folders.md Phase 2 found this filter missing here: pre-existing, not a
#                Phase 2 regression, but required for GameplayStep's gate-object parity).


def _land_tiles(zones):
    ts = set()
    for z in zones.values():
        terrain = ZE.TNAME.get(z["terrain_type"])
        if terrain in (None, "water", "rock") or z["area"] < MIN_AREA:
            continue
        ts.update(z["tiles_set"])
    return ts


def _rim8(zones):
    """All tiles that have an 8-neighbour in a different zone (the inter-zone rim)."""
    NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
    label = {}
    for zid, z in zones.items():
        for t in z["tiles_set"]:
            label[t] = zid
    rim = set()
    for (x, y), zid in label.items():
        for dx, dy in NB8:
            nb = (x + dx, y + dy)
            if label.get(nb, zid) != zid:
                rim.add((x, y))
                break
    return frozenset(rim)


class GateStep(PipelineStep):
    """Place Subterranean Gate pairs (no-op when ``state.subterrain`` is False).

    Config:
        seed  RNG seed (should match the seed used for terrain generation).

    Reads ``state.zones`` and writes:
      - ``state.gate_objs``   — pre-placed gate objects for both levels
      - ``state.gate_occ``    — occupied tile sets per level
      - ``state.gate_blk``    — blocked tile sets per level
      - ``state.gate_appr``   — approach tile tuples per level
    """

    def __init__(self, seed: int = 3) -> None:
        self.seed = seed

    def run(self, state: MapState, ontology) -> None:
        if not state.subterrain:
            return

        zones0 = state.zones[0]
        zones1 = state.zones[1]
        ts0 = _land_tiles(zones0)
        ts1 = _land_tiles(zones1)

        (gobjs0, gate_occ0, gate_blk0, gate_appr0), \
        (gobjs1, gate_occ1, gate_blk1, gate_appr1) = PG.place_gates(
            ts0, ts1, set(), set(),
            appr0=_rim8(zones0), appr1=_rim8(zones1),
            seed=self.seed,
        )
        print(f"  gates: {len(gobjs0)} Subterranean Gate pair(s) placed")

        state.gate_objs = gobjs0 + gobjs1
        state.gate_occ[0] = gate_occ0
        state.gate_occ[1] = gate_occ1
        state.gate_blk[0] = gate_blk0
        state.gate_blk[1] = gate_blk1
        state.gate_appr[0] = gate_appr0
        state.gate_appr[1] = gate_appr1
