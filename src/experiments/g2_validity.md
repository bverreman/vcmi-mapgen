# G2 — Validity: generated maps are playable (spec)

Gate doc: `research/G2_validity.md`

## Hypothesis

With reachability repair removing all overlapping blockers per tile and density
targets capped to DEFAULT_DENSITY for QUEST_GATE / WATER_TRANSPORT /
TERRAIN_MODIFIER, every plain `realize(seed)` call produces a map where:

- every zone, town, and mine is reachable on foot from the start town (BFS
  through passable land + subterranean-gate links), AND
- every purpose with corpus rate ≥ 0.2/1k lands within [0.5, 2.0]× that rate.

Expected: pass-rate ≥ 0.9 over K ≥ 10 seeds.

## Success thresholds (all required for PASS)

| Criterion | Requirement |
|-----------|-------------|
| Seeds | K ≥ 10 |
| Pass-rate | ≥ 0.9 (each seed: traverse ok AND density in band) |

## Fixes applied

### Fix 1 — GAMEPLAY_PUR additions (deps_realize.py)
`QUEST_GATE`, `WATER_TRANSPORT`, `TERRAIN_MODIFIER` added to `GAMEPLAY_PUR` so
they are tracked and filled toward density targets.

### Fix 2 — DEFAULT_DENSITY cap for over-estimated purposes (deps_realize.py)
PRIOR_DENSITY overestimates those three purposes by ~2.4×. Targets for
`QUEST_GATE`, `WATER_TRANSPORT`, `TERRAIN_MODIFIER` are computed from
`DEFAULT_DENSITY` (same denominator as benchmark RATE), not from PRIOR_DENSITY.

### Fix 3 — TRANSPORT density post-processing cap (deps_realize.py)
Portal loop could over-place TRANSPORT objects (all counted as subterranean
gates). Post-processing cap: remove excess level-0 TRANSPORT objects from the
tail of the object list (portal-loop portals placed last), keeping the ug_xy
entrance gate (placed earliest, smallest index). Removes at most
`2 × DEFAULT_DENSITY(TRANSPORT) × tiles / 1000` objects.

### Fix 4 — Map-edge mine guard in emit() (deps_realize.py)
Mines placed at the map boundary where all A-cell approach directions are either
out-of-bounds or covered by the mine's own B cells are now rejected at emit()
time. Prevents inaccessible mines placed on the last row/column.

### Fix 5 — Mine approach unblocking post-processing (deps_realize.py)
After object placement, mines whose A-cell approaches are entirely surrounded by
exactly one non-mine blocker have that blocker removed.

### Fix 6 — Reachability repair multi-blocker fix (deps_realize.py)
`reachability_repair` tracked only the FIRST removable object blocking each
tile. Because decorations can overlap, removing one blocker left secondary
blockers in place, so the carved path was still blocked in traverse.py.

Fix: `all_removable[(cx, cy)]` tracks ALL removable object indices per tile.
Repair traceback uses `to_remove.update(all_removable.get((x, y), ...))` to
remove every decoration covering each carved tile, guaranteeing that the
repaired path is clear in the final map.

### Fix 7 — Repair distance limit raised (deps_realize.py)
Dijkstra distance cap raised from 10 to 50, allowing repair to reach objects in
pockets up to 50 decoration-removal steps from the main component.

## Anti-shortcut flags

- Reachability uses the real `traverse.traverse()` BFS; no proxy.
- Density band is fixed [0.5, 2.0]×; not widened to pass.

## Measurements (10 seeds)

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| seeds | 10 | ≥ 10 | PASS |
| pass_rate | 1.0 | ≥ 0.9 | PASS |

All 10 seeds: traverse ok=True, all density bands satisfied.
