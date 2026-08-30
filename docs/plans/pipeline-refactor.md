# Pipeline Refactor Plan

Introduce a `VcmiMapGenPipeline` with configurable `Step` objects, clean up
`sys.path` hacks, and extract rendering into separate objects.

---

## Goals

1. Replace the monolithic `pp_map.build()` function with an explicit, step-by-step
   pipeline whose steps are configurable objects.
2. Retire the `pp_*.py` module family — their logic migrates into `vcmi_mapgen/steps/`.
3. Extract rendering (PNG, vmap) into `vcmi_mapgen/renderers/` — rendering is not a
   pipeline step; it consumes a finished (or partial) `MapState`.
4. Remove all `sys.path.insert` hacks and bare sibling imports across the package.
5. Leave the zone-rebuilding engine (`zone_engine.py` extract/rebuild/inspect CLI) and
   all domain modules untouched.

---

## Target structure

```
vcmi_mapgen/
  pipeline.py          # VcmiMapGenPipeline, MapState, PipelineStep
  steps/
    __init__.py
    terrain_gen.py     # TerrainGenStep
    tile.py            # TileStep
    segment.py         # SegmentStep
    gate.py            # GateStep
    gameplay.py        # GameplayStep
    pickup.py          # PickupStep
    vegetation.py      # VegetationStep
    repair.py          # RepairStep
  renderers/
    __init__.py
    png.py             # PngRenderer
    vmap.py            # VmapRenderer

  # Domain modules — imports cleaned, no other changes:
  ontology.py, terrain_segment.py, obj_resolve.py, faithful.py,
  vmapwrite.py, traverse.py, render.py, render_editor.py,
  zone_field.py, zone_skeleton.py, macro_topo.py,
  h3m.py, h3m2vmap.py, extract_faithful.py, vcmi_ids.py, vcmi_paths.py

  # Retired:
  pp_map.py, pp_gameplay.py, pp_pickup.py, pp_sample.py, pp_stats.py
```

---

## Phase 0 — Import cleanup (prerequisite for everything else)

**What:** Replace every `sys.path.insert(...)` + bare sibling import with a proper
package-relative import. This is a mechanical change with no behaviour difference.

**Files affected** (10+):

| File | Current | After |
|---|---|---|
| `zone_engine.py` | `import faithful as FA` | `from vcmi_mapgen import faithful as FA` |
| `pp_map.py` | `import macro_topo as MTOPO` | `from vcmi_mapgen import macro_topo as MTOPO` |
| `pp_gameplay.py` | `import ontology as ON` | `from vcmi_mapgen import ontology as ON` |
| `pp_pickup.py` | `import ontology as ON` | `from vcmi_mapgen import ontology as ON` |
| `macro_topo.py` | bare sibling imports | package-relative |
| `mapeval.py` | bare sibling imports | package-relative |
| `markov_terrain.py` | bare sibling imports | package-relative |
| `obj_resolve.py` | bare sibling imports | package-relative |
| `faithful.py` | bare sibling imports | package-relative |
| `h3m2vmap.py` | bare sibling imports | package-relative |
| `extract_faithful.py` | bare sibling imports | package-relative |
| `ontology.py` | `sys.path.insert` in `--regen` guards | remove; use package import |

**Also:** Replace all `ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`
path-building with `importlib.resources` or `pathlib.Path(__file__).parent.parent`.

**Verification:** `uv run pytest` green; `uv run python -m vcmi_mapgen.zone_engine run "All for One"` completes; `uv run python -m vcmi_mapgen.pp_map` completes.

---

## Phase 1 — `MapState` and `PipelineStep` (`pipeline.py`)

Create `vcmi_mapgen/pipeline.py`. No existing file is modified in this phase.

### `MapState` dataclass

```python
@dataclass
class MapState:
    # Set by TerrainGenStep
    seed: int = 0
    size: int = 72
    subterrain: bool = False
    water_mode: str = "normal"
    grids: dict[int, list] = field(default_factory=dict)   # level -> 2D terrain-code grid

    # Set by TileStep
    cells: dict[int, list] = field(default_factory=dict)   # level -> 2D cell-dict grid
    surfs: dict[int, list] = field(default_factory=dict)   # level -> 2D tile-string grid

    # Set by SegmentStep
    zones: dict[int, dict] = field(default_factory=dict)   # level -> zone dict

    # Set by GateStep
    gate_objs: list = field(default_factory=list)          # pre-placed gate objects
    gate_occ: dict[int, frozenset] = field(default_factory=dict)
    gate_blk: dict[int, frozenset] = field(default_factory=dict)
    gate_appr: dict[int, tuple] = field(default_factory=dict)

    # Set by GameplayStep / PickupStep / VegetationStep
    objs: list = field(default_factory=list)               # all placed objects, all levels
    targets: dict[int, set] = field(default_factory=dict)  # level -> target tiles
    zone_records: dict[int, dict] = field(default_factory=dict)
    player_zids: list = field(default_factory=list)        # [(level, zid), ...]
    player_towns: list = field(default_factory=list)

    # Cross-step budget tracking
    ledger: dict = field(default_factory=dict)

    # Diagnostic info accumulated by steps
    log: list[str] = field(default_factory=list)
```

### `PipelineStep` base class

```python
class PipelineStep:
    def run(self, state: MapState, ontology) -> None:
        raise NotImplementedError
```

Steps mutate `state` in-place and return `None`. The `ontology` argument is the
shared `Ontology` instance owned by the pipeline.

### `VcmiMapGenPipeline`

```python
class VcmiMapGenPipeline:
    def __init__(self, ontology):
        self._ontology = ontology
        self._steps: list[PipelineStep] = []

    def add_step(self, step: PipelineStep) -> "VcmiMapGenPipeline":
        self._steps.append(step)
        return self

    def run(self, until: PipelineStep | None = None) -> MapState:
        state = MapState()
        for step in self._steps:
            step.run(state, self._ontology)
            if until is not None and step is until:
                break
        return state
```

`add_step` returns `self` to allow optional chaining. `until=` accepts the **step
instance** that was added; the pipeline stops (inclusive) after that step.

---

## Phase 2 — Steps (one file per step)

Each step file in `vcmi_mapgen/steps/` imports from the relevant domain modules using
package-relative imports. The implementation is lifted from `pp_map.build()` and the
`pp_*.py` files with minimal restructuring — the goal is to move, not rewrite.

### `steps/terrain_gen.py` — `TerrainGenStep`

**Source:** `macro_topo.generate()` calls in `pp_map.build()`.

**Config (constructor):**
- `size: int = 72`
- `seed: int = 3`
- `water: float | None = None`
- `water_mode: str = "normal"` (`"none"` | `"normal"` | `"islands"`)
- `subterrain: bool = False`

**run:** Calls `macro_topo.generate()` for level 0 (and level 1 if `subterrain`),
handles gate-site carving (`_gate_anchor_points` / `_carve_gate_sites`), writes:
- `state.seed`, `state.size`, `state.subterrain`, `state.water_mode`
- `state.grids[0]`, `state.grids[1]` (if subterrain)
- `state.gate_objs` (pre-carved anchor set, for GateStep)

**Moves from:** `pp_map.build()` lines up to first `ZE.tile_terrain()` call;
`_gate_anchor_points`, `_carve_gate_sites` helpers from `pp_map.py`.

---

### `steps/tile.py` — `TileStep`

**Source:** `zone_engine.tile_terrain()` + grid/surf extraction in `pp_map.build()`.

**Config:** none (deterministic given `state.grids`).

**run:** For each active level, calls `ZE.tile_terrain(grid, W, H)`, extracts the
terrain-code grid and surface-string grid. Writes:
- `state.cells[level]`, `state.surfs[level]`, `state.grids[level]` (updated terrain codes)

**Moves from:** `pp_map.build()` `tile_terrain` + grid rebuild block.

---

### `steps/segment.py` — `SegmentStep`

**Source:** `zone_engine._segment_level()` + `_warn_sliver_zones()` in `pp_map.build()`.

**Config:** none.

**run:** For each active level, calls `ZE._segment_level(cells)`. Writes:
- `state.zones[level]`

Calls `pp_map._warn_sliver_zones()` (helper stays in `repair.py` or a shared utility).

**Moves from:** `pp_map.build()` segmentation block; `_warn_sliver_zones` from `pp_map.py`.

---

### `steps/gate.py` — `GateStep`

**Source:** `pp_gameplay.place_gates()` in `pp_map.build()`.

**Config:**
- `seed: int = 3`

**run:** No-op if `state.subterrain` is False. Otherwise calls `PG.place_gates()` with
the rim sets from both levels. Writes:
- `state.gate_objs` (the gate object lists)
- `state.gate_occ[0/1]`, `state.gate_blk[0/1]`, `state.gate_appr[0/1]`

**Moves from:** `pp_map.build()` gate-placement block.

---

### `steps/gameplay.py` — `GameplayStep`

**Source:** `pp_gameplay.place_zone()`, `pp_map.select_player_zones()`,
`pp_map._run_level()`, `pp_map.seal_zone_borders()`.

**Config:**
- `seed: int = 3`
- `players: int = 0`

**run:** Calls `select_player_zones()`, initialises `ledger`, calls `_run_level()` for
each active level (which calls `place_zone()` per zone and `seal_zone_borders()`).
Writes:
- `state.objs` (gameplay objects)
- `state.targets[level]`, `state.zone_records[level]`
- `state.player_zids`, `state.player_towns` (partial — towns only)
- `state.ledger`

**Moves from:** `pp_map.build()` gameplay block; `pp_map._run_level()`;
`pp_map.select_player_zones()`; `pp_map.seal_zone_borders()`;
all of `pp_gameplay.py`; `pp_stats.py` (absorbed as internal helpers).

---

### `steps/pickup.py` — `PickupStep`

**Source:** `pp_pickup.place_pickups()`, `pp_pickup.place_scatter()`,
`pp_pickup.place_water()` called from `pp_map._run_level()`.

**Config:**
- `seed: int = 3`

**run:** Iterates zones per level, calls the pickup/scatter/water placement functions.
Extends `state.objs`.

**Moves from:** All of `pp_pickup.py`; the pickup-dispatch slice of `pp_map._run_level()`.

---

### `steps/vegetation.py` — `VegetationStep`

**Source:** `pp_sample.place_zone_vegetation()` called from `pp_map._run_level()`.

**Config:**
- `seed: int = 3`

**run:** Iterates zones per level, calls vegetation placement. Extends `state.objs`.

**Moves from:** All of `pp_sample.py`; the vegetation-dispatch slice of `pp_map._run_level()`.

---

### `steps/repair.py` — `RepairStep`

**Source:** `pp_map._repair_and_finish_level()`, `pp_map.rescue_unreachable_zones()`,
`pp_map.g2_repair()`, `pp_map.fill_open_islands()`.

**Config:**
- `seed: int = 3`

**run:** Calls `rescue_unreachable_zones()`, then `_repair_and_finish_level()` for each
active level (G2 repair, island fill, pocket caches, seerhut dedup). Updates
`state.objs` in-place. Appends diagnostic lines to `state.log`.

**Moves from:** `pp_map._repair_and_finish_level()`; `pp_map.rescue_unreachable_zones()`;
`pp_map.g2_repair()`; `pp_map.fill_open_islands()`.

---

## Phase 3 — Renderers (`renderers/`)

Create `vcmi_mapgen/renderers/`. Renderers are not steps; they read a `MapState`
and produce output.

### `renderers/png.py` — `PngRenderer`

```python
class PngRenderer:
    def __init__(self, out_dir: str | None = None):
        ...

    def render(self, state: MapState, level: int = 0, title: str = "") -> Image:
        """Return a PIL Image for the given level."""

    def save(self, state: MapState, path: str, level: int = 0) -> str:
        """Render and save to path. Returns the path."""
```

**Moves from:** `pp_map.gen_one()` render block; delegates to existing `render_editor.render_map()`.

### `renderers/vmap.py` — `VmapRenderer`

```python
class VmapRenderer:
    def render(self, state: MapState, path: str, name: str = "pp-map",
               teams_spec: str = "ffa") -> str:
        """Write a .vmap file. Returns the path."""
```

**Moves from:** `pp_map.export_vmap()`, `pp_map.apply_playability()`,
`pp_map.parse_teams()`.

---

## Phase 4 — CLI update and `pp_map.py` retirement

**`pp_map.py`:** The `main()` function is replaced with a thin wrapper that builds and
runs the pipeline, then calls the renderers. Once that is verified, `pp_map.py` is
deleted and `vcmi_mapgen/__main__.py` (or a new `cli.py`) becomes the entry point.

**`zone_engine.py`:** Its `run` / `rebuild` / `extract` / `inspect` CLI subcommands are
unchanged. Internal functions (`tile_terrain`, `_segment_level`) that steps now import
stay as package-internal helpers with no API change.

**`pyproject.toml`:** Update `[project.scripts]` entry point if it pointed at `pp_map:main`.

---

## Contracts that must not break

| Contract | How to verify |
|---|---|
| Identity guarantee | `uv run python -m vcmi_mapgen.zone_engine rebuild "All for One" --identity --verify` prints `IDENTITY OK` |
| Rendering tests | `uv run pytest` green |
| pp-map generation | `uv run python -m vcmi_mapgen.pp_map` (or new CLI) produces PNG + vmap without error |
| Determinism | Same seed → same output before and after the refactor |

---

## Migration order

1. **Phase 0** — import cleanup (no behaviour change; verifiable immediately with pytest)
2. **Phase 1** — `pipeline.py` (new file, nothing broken yet)
3. **Phase 2** — migrate one step at a time, starting with `TerrainGenStep` (most isolated).
   After each step: wire it into a test pipeline and verify its output matches the old
   `build()` output for the same seed.
4. **Phase 3** — renderers (replace `gen_one()` render block)
5. **Phase 4** — retire `pp_map.py`, update CLI entry point

Each phase is independently committable and verifiable before the next begins.
