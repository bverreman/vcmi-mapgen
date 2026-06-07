# PROGRESS

Status of each gate. The engine appends results here. Statuses: NOT_STARTED,
IN_PROGRESS, PASS, WEAK_PASS, FAIL, KILLED, REOPENED.

| Gate | Status | Last measured | Notes |
|------|--------|---------------|-------|
| G0 | PASS | mean_objdist 3.66, pass_rate 0.0 (--maps 8 --seeds 4, 2026-06-06) | all 4 harness criteria satisfied; deterministic, fair shuffled-control confirmed |
| G1 | PASS | mean obj-dist 2.44, ctrl 3.48, margin +1.04 (n=12, 10 seeds, 2026-06-07) | rework-2: commit footprint fix + experiment harness passed all criteria |
| G2 | PASS | pass-rate 1.0 (10/10 seeds, 2026-06-07) | multi-blocker repair fix + density caps; all traverse ok + density in band |
| G3 | FAIL | obj-dist 3.01 vs ctrl 2.67 (All for One) | control beats graph; depends on G1 |

## Log

- 2026-06: program scaffolded. Smoke benchmark (--maps 2 --seeds 2) recorded above.
  Real runs use --maps 12 --seeds 10.
- 2026-06-06: G0 PASS. Reviewer re-ran --maps 8 --seeds 4; all 4 criteria met,
  determinism verified, anti-shortcut flags clear, pytest 10/10.
- 2026-06-07: G1 PASS (rework-2). Full benchmark --maps 12 --seeds 10: mean_objdist
  2.44 (≤2.5), 83% within 3.0 (≥80%), control margin +1.04 (≥1.0). All criteria met.
  Footprint fix + experiment harness works. Advance to G2 validation.
- 2026-06-07: G2 PASS. pass_rate 1.0 (10/10 seeds). Root cause of reachability failures:
  `reachability_repair` tracked only the first removable blocker per tile via `owner`;
  overlapping decorations left secondary blockers after repair so carved paths remained
  blocked in traverse.py. Fix: `all_removable[(cx,cy)]` tracks all removable object
  indices per tile; traceback uses `to_remove.update(all_removable.get(...))`. Also
  fixed: GAMEPLAY_PUR additions (QUEST_GATE/WATER_TRANSPORT/TERRAIN_MODIFIER), DEFAULT_DENSITY
  caps for those 3 purposes, TRANSPORT post-processing cap, map-edge mine guard in emit(),
  mine approach unblocking post-processing, repair distance cap raised to 50.
  Spec: g2_validity.md. Harness: g2_validity.py. Tests: 26/26 pass.
