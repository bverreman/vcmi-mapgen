"""VegetationStep — place terrain-matched decorative vegetation per zone.

Phase 2 note: vegetation sampling (pp_sample.sample_zone) is currently
integrated into _run_level() inside GameplayStep. Extracting it into a proper
separate step requires splitting _run_level() and is deferred to Phase 4.
This step is a no-op placeholder; include it in the pipeline for forward
compatibility.
"""
from __future__ import annotations

from vcmi_mapgen.pipeline import MapState, PipelineStep


class VegetationStep(PipelineStep):
    """Placeholder — vegetation placement is handled by GameplayStep in Phase 2.

    In Phase 4 this step will call pp_sample.sample_zone() directly per zone,
    once _run_level() is split into distinct gameplay / pickup / vegetation passes.

    Config:
        seed  RNG seed (reserved for Phase 4).
    """

    def __init__(self, seed: int = 3) -> None:
        self.seed = seed

    def run(self, state: MapState, ontology) -> None:
        pass  # handled by GameplayStep._run_level() in Phase 2
