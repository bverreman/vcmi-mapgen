# Pipeline Refactor Plan v2 — folder-per-step

Builds on `pipeline-refactor.md` (v1), which got as far as: `pipeline.py` exists, all 8
steps exist as flat files in `steps/`, `renderers/` exists, and `pp_map.py` gained an
opt-in `--use-pipeline` flag ("Phase 4" per its commit message) — but the flag's existence
was mistaken for retirement. In truth `GameplayStep`/`RepairStep` still delegate to
`pp_map.py`/`pp_gameplay.py` functions, and `PickupStep`/`VegetationStep` are no-op
placeholders; all the real per-zone content-placement logic still lives in `pp_map.py`'s
monolithic `_run_level()`. This plan finishes that migration and reshapes it: **each step
becomes a folder (`steps/<name>/`) with `step.py` holding the `PipelineStep` subclass and
its private logic/stats modules living alongside it**, instead of one flat file per step.

v1 stays as the historical record of Phases 0-4 as originally conceived; this document is
the one to follow going forward.

---

## Goals

1. Finish moving `pp_map.py` / `pp_gameplay.py` / `pp_pickup.py` / `pp_sample.py` /
   `pp_stats.py`'s logic into `vcmi_mapgen/steps/`, split by real per-step ownership (several
   of these files split *across* two step folders — see "Function-to-folder map" below).
2. Reshape `steps/` from flat files to folders: `steps/<name>/step.py` + sibling logic
   modules + sibling `*_test.py` files, so everything a step needs lives under it.
3. Fix the step *registration order* — `Gameplay → Vegetation → Pickup → Repair`, not
   today's placeholder `Gameplay → Pickup → Vegetation` (vegetation's `blocked` output for a
   zone is a hard input to that same zone's pickup-scatter pass; the placeholder order was
   never exercised because both steps are still no-ops).
4. Introduce a typed, shared-by-reference **`PlacementWorkspace`** for the inter-step
   handoff data that doesn't belong on `MapState` (protected web, entrance plan,
   occupied/blocked/approach sets, seal/hard-avoid, ridge, guard tiles). `MapState` stays
   generic map-layer truth only; steps that need to collaborate share this object instead,
   injected via constructor — not threaded through `state.extras` and not a raw dict.
5. Retire `pp_map.py`, `pp_gameplay.py`, `pp_pickup.py`, `pp_sample.py`, `pp_stats.py`
   entirely, once parity is proven (see "Parity gate" below).
6. Leave the zone-rebuilding engine (`zone_engine.py` extract/rebuild/inspect CLI) and all
   other domain modules untouched, except for the two call sites that currently call
   `pp_map.build()` directly (`zone_engine.cmd_generate`, `render_zone_overlay.py`).

---

## Target structure

```
vcmi_mapgen/
  pipeline.py                   # MapState, PipelineStep, VcmiMapGenPipeline (unchanged)
                                 # + new: PlacementWorkspace, LevelWorkspace, ZoneWorkspace
  steps/
    __init__.py                 # re-exports each folder's Step class
    terrain_gen/
      step.py                    # TerrainGenStep
      macro_topo.py               # moved in from vcmi_mapgen/macro_topo.py (single owner
                                   # once pp_map.py's own import of it is gone)
      step_test.py
      macro_topo_test.py
    tile/
      step.py                    # TileStep (stays thin; zone_engine.tile_terrain is shared
                                   # domain infra, stays top-level)
      step_test.py
    segment/
      step.py                    # SegmentStep
      step_test.py
    gate/
      step.py                    # GateStep
      gates.py                    # place_gates, mine_gate_stats, GATE_ANIM — split out of
                                   # pp_gameplay.py
      step_test.py / gates_test.py
    gameplay/
      step.py                    # GameplayStep — absorbs pp_map._run_level's Pass-1 loop
                                   # (place_zone + protected-web computation) and
                                   # select_player_zones
      mines.py                    # mine_gameplay, place_zone, theta_covariates, intensity
                                   # helpers, BASIC_MINE_RES / purpose tuples / CAPS —
                                   # split out of pp_gameplay.py
      water.py                    # place_water + _ensure_water_seaports (+ seaport
                                   # blocking/approach helpers) — moved here for placement
                                   # TIMING/rigidity even though place_water's old address
                                   # was pp_pickup.py
      step_test.py / mines_test.py / water_test.py
    vegetation/
      step.py                    # VegetationStep — real per-zone sample_zone work,
                                   # writes blocked/open_set into PlacementWorkspace
      stats.py                    # all of pp_stats.py
      sample.py                   # all of pp_sample.py
      step_test.py / stats_test.py / sample_test.py
    pickup/
      step.py                    # PickupStep — place_scatter per zone (reads workspace's
                                   # blocked/open_set from Vegetation), then the global
                                   # place_loot_zones pass
      scatter.py                  # place_scatter, place_pickups — from pp_pickup.py
      loot_zones.py                # place_loot_zones — from pp_pickup.py
      step_test.py / scatter_test.py / loot_zones_test.py
    repair/
      step.py                    # RepairStep — same sequencing as legacy
                                   # _repair_and_finish_level + the border-seal tail of
                                   # _run_level
      geometry.py                  # g2_repair, fill_open_islands, rescue_unreachable_zones
                                   # — from pp_map.py (these were always pp_map-private,
                                   # not from any pp_* layer module)
      border_seal.py               # seal_zone_borders — from pp_map.py
      caches.py                    # place_pocket_caches, place_seer_hut_quests — from
                                   # pp_pickup.py (already only ever called from
                                   # _repair_and_finish_level, never from _run_level itself)
      step_test.py / geometry_test.py / border_seal_test.py / caches_test.py
  renderers/                     # unchanged
  # Domain modules staying top-level (multi-owner, or their own standalone CLI — not
  # step-private):
  ontology.py, terrain_segment.py, obj_resolve.py, faithful.py, zone_engine.py,
  zone_field.py, zone_skeleton.py, mapeval.py, vmapwrite.py, traverse.py,
  h3m.py, h3m2vmap.py, extract_faithful.py, vcmi_ids.py, vcmi_paths.py

  # Retired once the parity gate passes:
  pp_map.py, pp_gameplay.py, pp_pickup.py, pp_sample.py, pp_stats.py, pp_test.py
```

**Why these stay top-level and don't move into a step folder:** `zone_engine.py`,
`terrain_segment.py`, `faithful.py` are each imported by 2+ steps *and* by non-step code
(rendering, the extract/rebuild CLI) — the "single step owns this" bar doesn't clear.
`zone_field.py` and `zone_skeleton.py` are each also their own standalone research CLI
(`python -m vcmi_mapgen.zone_field --map ... --zone N`) independent of the generation
pipeline; `zone_field.protected_web`/`edge_dist`/`plan_entrances` get *called* by
`GameplayStep`, but the module itself isn't gameplay-private. `mapeval.py` is a standalone
scoring tool unrelated to generation. `macro_topo.py` is the one exception that DOES move:
once `pp_map.py`'s own (legacy, soon-deleted) import of it is gone, `TerrainGenStep` is its
only caller.

---

## `PlacementWorkspace` — the inter-step collaboration object

Not part of `MapState`. Lives in `pipeline.py` next to `MapState`/`PipelineStep` since it's
pipeline-collaboration machinery, not any one step's private domain logic. Constructed once
per pipeline run (by whoever wires the steps together — today that's
`pp_map._pipeline_gen_one`, tomorrow it's `zone_engine.cmd_generate` or a slim `cli.py`),
and passed **by reference, via constructor**, into `GameplayStep`, `VegetationStep`,
`PickupStep`, `RepairStep` only — `TerrainGenStep`/`TileStep`/`SegmentStep`/`GateStep` don't
need it, they already communicate cleanly through `MapState` fields (`grids`, `cells`,
`zones`, `gate_occ`/`gate_blk`/`gate_appr`).

```python
@dataclass
class ZoneWorkspace:
    """One zone's handoff data, mutated in place as Gameplay -> Vegetation -> Pickup ->
    Repair each run. Not a MapState field: this is step-collaboration bookkeeping, not a
    map-level fact anything outside these four steps needs to read."""
    terrain: str = ""
    ts: frozenset = frozenset()          # set by GameplayStep
    ts_full: frozenset = frozenset()
    occupied: frozenset = frozenset()
    gblocked: frozenset = frozenset()
    approaches: tuple = ()
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
    zones: dict[int, ZoneWorkspace] = field(default_factory=dict)   # zid -> ZoneWorkspace
    entrance_plan: dict = field(default_factory=dict)
    ridge: frozenset = frozenset()
    seal_avoid: set = field(default_factory=set)
    hard_avoid: set = field(default_factory=set)
    guard_tiles: frozenset = frozenset()

class PlacementWorkspace:
    def __init__(self):
        self.levels: dict[int, LevelWorkspace] = {}   # level -> LevelWorkspace
```

Field names/shape are a starting proposal — adjustable during implementation as long as the
principle holds: typed objects held by reference, not a dict blob, and not leaked into
`MapState` or `pipeline.py`'s step-agnostic machinery.

---

## Function-to-folder map (what moves where, and why)

| Source | Function(s) | Destination | Why |
|---|---|---|---|
| `pp_gameplay.py` (prelude constants/helpers, lines ~28-124: stats paths, purpose tuples, `gate_dist`, `openness`, etc.) | | Promote to a real top-level domain module (peer of `ontology.py`) if genuinely domain data shared by Gate+Gameplay; otherwise split to whichever of the two owns it | These are used by both Gate and Gameplay — not narrow enough to belong to one folder, not broad enough to justify a generic `_shared/` catch-all |
| `pp_gameplay.py` | `place_gates`, `mine_gate_stats`, `GATE_ANIM` | `steps/gate/gates.py` | Gate-only |
| `pp_gameplay.py` | `mine_gameplay`, `place_zone`, `theta_covariates`, `_cells`, `_fits`, `_intensity_weights`, `_info_pool`, `BASIC_MINE_RES`, `CAPS`, `TOWN_MIN_AREA`, purpose tuples | `steps/gameplay/mines.py` | Gameplay-only |
| `pp_map.py` | `select_player_zones` | `steps/gameplay/step.py` (or a small `players.py`) | Already Gameplay-only today |
| `pp_pickup.py` | `place_water` | `steps/gameplay/water.py` | Runs at gameplay-time (before vegetation forbids its footprint), despite its old file name |
| `pp_map.py` | `_ensure_water_seaports`, seaport blocking/approach helpers | `steps/gameplay/water.py` | Same timing reason |
| `pp_map.py` | `_run_level`'s Pass-1 loop (gameplay dispatch + `zone_field.protected_web` call) | `steps/gameplay/step.py` | Becomes `GameplayStep.run()`'s zone loop, writing into `PlacementWorkspace` instead of a local `zone_cache` dict |
| `pp_sample.py` | everything | `steps/vegetation/sample.py` | Vegetation-only |
| `pp_stats.py` | everything | `steps/vegetation/stats.py` | Vegetation-only (only ever imported by `pp_sample.py`) |
| `pp_map.py` | `_run_level`'s Pass-2 vegetation half (`PP.sample_zone` dispatch) | `steps/vegetation/step.py` | Becomes `VegetationStep.run()` |
| `pp_pickup.py` | `place_scatter`, `place_pickups`, `_pick`, `_legal`, `_reach8`, `_web_dist` | `steps/pickup/scatter.py` | Pickup-only |
| `pp_pickup.py` | `place_loot_zones`, `_dedupe_pockets` (loot-zone specific) | `steps/pickup/loot_zones.py` | Pickup-only, but a *global* per-level pass — runs after every zone's scatter |
| `pp_map.py` | `_run_level`'s Pass-2 scatter half (`PK.place_scatter` dispatch) + the `place_loot_zones` tail | `steps/pickup/step.py` | Becomes `PickupStep.run()` — reads `blocked`/`open_set` that `VegetationStep` wrote into `PlacementWorkspace` |
| `pp_map.py` | `g2_repair`, `fill_open_islands`, `rescue_unreachable_zones`, `_terrain_reach` | `steps/repair/geometry.py` | Already Repair-only today (pp_map-private helpers, never from a `pp_*` layer module) |
| `pp_map.py` | `seal_zone_borders` | `steps/repair/border_seal.py` | Places no content — only structural/guard fixes; matches Repair's charter, not Pickup's |
| `pp_pickup.py` | `place_pocket_caches`, `place_seer_hut_quests` | `steps/repair/caches.py` | Already only ever called from `_repair_and_finish_level`, never from `_run_level` |
| `pp_map.py` | `_repair_and_finish_level` (orchestration + guard-dedup logic) | `steps/repair/step.py` | Becomes `RepairStep.run()` |
| `macro_topo.py` | everything | `steps/terrain_gen/macro_topo.py` | Single owner once legacy `pp_map.py` import is gone |
| `pp_test.py` | tests for `pp_stats`/`pp_sample`/`macro_topo`/etc. | redistributed into the relevant `steps/<name>/*_test.py` | Matches the "test sits beside its module" convention, now inside the folder |

---

## Migration order (each phase independently committable and verifiable)

### Phase 0 — Container reshape (mechanical, no behavior change)
- Create the 8 `steps/<name>/` folders; move each existing flat `steps/<name>.py` into
  `steps/<name>/step.py` unchanged. Update `steps/__init__.py` imports.
- Move `macro_topo.py` into `steps/terrain_gen/macro_topo.py`; update `steps/terrain_gen/step.py`'s
  import. (`pp_map.py`'s own import of `macro_topo` gets updated to the new path too, until
  Phase 6 deletes it.)
- Add `PlacementWorkspace`/`LevelWorkspace`/`ZoneWorkspace` to `pipeline.py` — unused for now,
  zero behavior change.
- **Verify:** `uv run pytest` green; `uv run python -m vcmi_mapgen.zone_engine rebuild "All for One" --identity --verify` → `IDENTITY OK`; `uv run python -m vcmi_mapgen.pp_map --use-pipeline --seed 3` completes.

### Phase 1 — `GateStep` (smallest real logic move)
- Split `place_gates`/`mine_gate_stats`/`GATE_ANIM` out of `pp_gameplay.py` into
  `steps/gate/gates.py`. `GateStep` imports from its own folder instead of top-level `pp_gameplay`.
- **Verify:** `--subterrain` generation, same seed, before/after byte-identical gate objects.

### Phase 2 — `GameplayStep`
- Move `mine_gameplay`/`place_zone`/helpers into `steps/gameplay/mines.py`; `place_water` +
  `_ensure_water_seaports` into `steps/gameplay/water.py`; `select_player_zones` into
  `steps/gameplay/step.py`.
- `GameplayStep.run()` absorbs `_run_level`'s Pass-1 loop, populating a `PlacementWorkspace`
  per zone (`occupied`/`gblocked`/`approaches`/`prot`/`rim8`/`ent_bands`) instead of a local
  `zone_cache` dict.
- **Verify:** with Vegetation/Pickup/Repair still delegating to legacy (or still no-ops),
  gameplay-only object set (towns/mines/dwellings/gates/seaports) matches legacy exactly for
  the same seed.

### Phase 3 — `VegetationStep`
- Move all of `pp_sample.py` → `steps/vegetation/sample.py`, all of `pp_stats.py` →
  `steps/vegetation/stats.py`.
- `VegetationStep.run()` iterates zones, reads `prot`/forbid inputs from the workspace
  (written by `GameplayStep`), calls `sample_zone`, writes `blocked`/`open_set` back into the
  same `ZoneWorkspace`.
- **Verify:** full object list (gameplay + vegetation) matches legacy exactly, same seed.

### Phase 4 — `PickupStep`
- Move `place_scatter`/`place_pickups` → `steps/pickup/scatter.py`; `place_loot_zones` →
  `steps/pickup/loot_zones.py`.
- `PickupStep.run()` reads `blocked`/`open_set` from the workspace (written by
  `VegetationStep`), runs per-zone scatter, then the global `place_loot_zones` pass; writes
  `seal_avoid`/`hard_avoid`/`reach`/`used` back into the workspace for `RepairStep`.
- **Verify:** full object list (gameplay + vegetation + pickup) matches legacy exactly, same seed.
- **Fixes registration order** at this point: pipeline becomes `Gameplay → Vegetation →
  Pickup → Repair`, replacing the placeholder's `Gameplay → Pickup → Vegetation`.

### Phase 5 — `RepairStep`
- Move `g2_repair`/`fill_open_islands`/`rescue_unreachable_zones` → `steps/repair/geometry.py`;
  `seal_zone_borders` → `steps/repair/border_seal.py`; `place_pocket_caches`/
  `place_seer_hut_quests` (from `pp_pickup.py`) → `steps/repair/caches.py`.
- `RepairStep.run()` reproduces `_repair_and_finish_level`'s exact sequencing plus the
  border-seal tail that today runs inside `_run_level`.
- **Verify — the parity gate:** for a representative seed/size/subterrain matrix, the new
  pipeline path and the legacy `pp_map.build()` path, given the same config and seed,
  produce **byte-identical map JSON**. Not "similar" — identical. Every zone already reseeds
  independently off the shared `seed` (not a running RNG carried across zones), so this
  restructuring shouldn't perturb a single draw; if it does, that's a signal something broke,
  not an acceptable "close enough." Any bug you already suspect in the legacy path gets fixed
  as a separate, explicitly-called-out change — never folded silently into this comparison.

### Phase 6 — Retirement and call-site updates
- Delete `pp_map.py`'s `build()`/`gen_one()`/`_run_level()`/legacy helpers and the
  `--use-pipeline` flag — the pipeline becomes the only path.
- Update `zone_engine.py`'s `generate --layout pp` (`cmd_generate`) to call the pipeline +
  renderers directly instead of `pp_map.build()`.
- Update `render_zone_overlay.py`'s direct `pp_map.build(...)` call the same way.
- Delete `pp_gameplay.py`, `pp_pickup.py`, `pp_sample.py`, `pp_stats.py` (grep first to
  confirm nothing else still imports them).
- Redistribute `pp_test.py`'s test cases into the relevant `steps/<name>/*_test.py` files
  per the function-to-folder map above; delete `pp_test.py`.
- `pyproject.toml`: update `[project.scripts]` if it pointed at `pp_map:main`.

---

## Contracts that must not break

| Contract | How to verify |
|---|---|
| Identity guarantee (unrelated to this refactor, but must stay green) | `uv run python -m vcmi_mapgen.zone_engine rebuild "All for One" --identity --verify` prints `IDENTITY OK` |
| Rendering tests | `uv run pytest` green |
| Determinism | Same seed → same output, before and after every phase |
| **Parity gate (Phase 5, before Phase 6 deletes anything)** | Same config + same seed → byte-identical map JSON, legacy path vs. new pipeline path |
| pp-map generation keeps working through every phase | `uv run python -m vcmi_mapgen.pp_map --use-pipeline --seed 3` (until Phase 6, when this flag disappears and becomes the only path) |

---

## Open implementation-time judgment calls (deliberately not pinned down further)

- Exact split of the `pp_gameplay.py` prelude constants (Gate vs. Gameplay vs. promoted to a
  top-level domain module) — resolve while implementing Phase 1/2, guided by "is this
  genuinely domain data or one step's private detail."
- Whether `select_player_zones` gets its own `players.py` or stays inline in
  `steps/gameplay/step.py` — a one-function call, either is fine.
- `PlacementWorkspace`'s exact field names/shape — the sketch above is a starting proposal,
  not a locked contract.
