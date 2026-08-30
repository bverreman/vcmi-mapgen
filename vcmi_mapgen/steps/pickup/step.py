"""PickupStep — scatter resources, artifacts, and pocket caches.

Phase 2 note: pickup placement (pp_pickup.place_pickups, place_scatter,
place_water, place_loot_zones) is currently integrated into _run_level()
inside GameplayStep. Extracting it into a proper separate step requires
splitting _run_level() and is deferred to Phase 4. This step is a no-op
placeholder; include it in the pipeline for forward compatibility.
"""
from __future__ import annotations

from vcmi_mapgen.pipeline import MapState, PipelineStep


class PickupStep(PipelineStep):
    """Placeholder — pickup placement is handled by GameplayStep in Phase 2.

    In Phase 4 this step will call pp_pickup.place_scatter/place_pickups/
    place_water/place_loot_zones directly, once _run_level() is split.

    Config:
        seed  RNG seed (reserved for Phase 4).
    """

    def __init__(self, seed: int = 3) -> None:
        self.seed = seed

    def run(self, state: MapState, ontology) -> None:
        pass  # handled by GameplayStep._run_level() in Phase 2
