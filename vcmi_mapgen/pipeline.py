"""Map-generation pipeline: MapState dataclass, PipelineStep base, VcmiMapGenPipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcmi_mapgen.ontology import Ontology  # type: ignore[attr-defined]


@dataclass
class MapState:
    """Mutable map state threaded through all pipeline steps."""

    # --- set by TerrainGenStep ---
    seed: int = 0
    size: int = 72
    subterrain: bool = False
    water_mode: str = "normal"
    # level -> 2-D list of terrain-code ints (row-major)
    grids: dict = field(default_factory=dict)

    # --- set by TileStep ---
    # level -> 2-D list of tile-dict objects ({"t":…, "view":…, …})
    cells: dict = field(default_factory=dict)
    # level -> 2-D list of tile-string objects (e.g. "gr2_")
    surfs: dict = field(default_factory=dict)

    # --- set by SegmentStep ---
    # level -> zone dict {zid: {tiles_set, terrain_type, area, centroid, …}}
    zones: dict = field(default_factory=dict)

    # --- set by GateStep ---
    # pre-placed subterranean gate objects (both levels combined before GameplayStep)
    gate_objs: list = field(default_factory=list)
    # level -> frozenset of occupied tiles from gates
    gate_occ: dict = field(default_factory=dict)
    # level -> frozenset of blocked tiles from gates
    gate_blk: dict = field(default_factory=dict)
    # level -> tuple of approach tile sets from gates
    gate_appr: dict = field(default_factory=dict)
    # set of (x,y) tiles that must not be overwritten during autotiling (tunnel protect)
    tunnel_protect: set = field(default_factory=set)

    # --- accumulated by GameplayStep / PickupStep / VegetationStep ---
    # all placed objects across all levels
    objs: list = field(default_factory=list)
    # level -> set of target tiles (gameplay approach tiles)
    targets: dict = field(default_factory=dict)
    # level -> per-zone records {zid: {ts, objs, open_set, …}}
    zone_records: dict = field(default_factory=dict)

    # --- set by GameplayStep (player placement) ---
    # [(level, zid), …] in player order
    player_zids: list = field(default_factory=list)
    # town objects in player order
    player_towns: list = field(default_factory=list)

    # --- cross-step economy tracking ---
    ledger: dict = field(default_factory=dict)

    # --- untyped bag for step-specific inter-step handoff data ---
    # Steps may store implementation-specific data here (e.g. ridge, border_guards)
    # to pass it to a downstream step without coupling MapState to that step pair.
    extras: dict = field(default_factory=dict)

    # --- diagnostic messages appended by steps ---
    log: list = field(default_factory=list)


@dataclass
class ZoneWorkspace:
    """One zone's handoff data, mutated in place as Gameplay -> Vegetation -> Pickup ->
    Repair each run. Not a MapState field: this is step-collaboration bookkeeping, not a
    map-level fact anything outside these four steps needs to read."""

    terrain: str = ""
    ts: frozenset = frozenset()          # set by GameplayStep
    ts_full: frozenset = frozenset()
    gobjs: list = field(default_factory=list)
    occupied: frozenset = frozenset()
    gblocked: frozenset = frozenset()
    approaches: tuple = ()
    entrances: list = field(default_factory=list)   # kit.topology.plan_entrances entries
    prot: frozenset = frozenset()         # protected web
    rim8: frozenset = frozenset()
    ent_bands: frozenset = frozenset()
    blocked: frozenset = frozenset()      # set by VegetationStep
    open_set: frozenset = frozenset()
    passable: frozenset = frozenset()
    reach: frozenset = frozenset()        # set by PickupStep
    used: frozenset = frozenset()


@dataclass
class LevelWorkspace:
    zones: dict = field(default_factory=dict)          # zid -> ZoneWorkspace
    entrance_plan: dict = field(default_factory=dict)
    ridge: frozenset = frozenset()
    seal_avoid: set = field(default_factory=set)
    hard_avoid: set = field(default_factory=set)
    guard_tiles: frozenset = frozenset()
    # seaport blocking/approach cells (set by GameplayStep) — vegetation must forbid them
    seaport_blk: frozenset = frozenset()
    seaport_appr: frozenset = frozenset()
    water_tiles: frozenset = frozenset()   # set by GameplayStep — needed by place_loot_zones
    town_of_zone: dict = field(default_factory=dict)   # set by GameplayStep — zid -> town obj


class PlacementWorkspace:
    """Inter-step collaboration object for Gameplay/Vegetation/Pickup/Repair. Shared by
    reference, injected via constructor — not threaded through ``state.extras`` and not a
    raw dict. ``MapState`` stays generic map-layer truth only."""

    def __init__(self) -> None:
        self.levels: dict = {}   # level -> LevelWorkspace


class PipelineStep:
    """Base class for all map-generation steps.

    Subclasses implement ``run(state, ontology)`` which mutates *state* in-place.
    The *ontology* argument is the shared Ontology instance owned by the pipeline.
    """

    def run(self, state: MapState, ontology) -> None:
        raise NotImplementedError(f"{type(self).__name__}.run() not implemented")


class VcmiMapGenPipeline:
    """Ordered list of steps that together produce a complete MapState.

    Usage::

        from vcmi_mapgen.ontology import Ontology
        from vcmi_mapgen.pipeline import VcmiMapGenPipeline, MapState
        from vcmi_mapgen.steps.terrain_gen.step import TerrainGenStep
        from vcmi_mapgen.steps.tile.step import TileStep
        # … add more steps …

        ont = Ontology()
        pipeline = VcmiMapGenPipeline(ontology=ont)
        terrain_step = TerrainGenStep(size=72, seed=3)
        tile_step = TileStep()
        pipeline.add_step(terrain_step)
        pipeline.add_step(tile_step)

        state = pipeline.run()                     # full run
        state = pipeline.run(until=terrain_step)   # stop after terrain
    """

    def __init__(self, ontology) -> None:
        self._ontology = ontology
        self._steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> "VcmiMapGenPipeline":
        """Append *step* to the pipeline and return self for optional chaining."""
        self._steps.append(step)
        return self

    def run(self, until: PipelineStep | None = None) -> MapState:
        """Execute steps in order, returning the resulting MapState.

        If *until* is given (a step instance previously added via :meth:`add_step`),
        execution stops **after** that step so the partial state can be inspected.
        """
        state = MapState()
        for step in self._steps:
            step.run(state, self._ontology)
            if until is not None and step is until:
                break
        return state
