# PROGRESS

Status of each gate. The engine appends results here. Statuses: NOT_STARTED,
IN_PROGRESS, PASS, WEAK_PASS, FAIL, KILLED, REOPENED.

| Gate | Status | Last measured | Notes |
|------|--------|---------------|-------|
| G0 | PASS | mean_objdist 3.66, pass_rate 0.0 (--maps 8 --seeds 4, 2026-06-06) | all 4 harness criteria satisfied; deterministic, fair shuffled-control confirmed |
| G1 | PASS | mean obj-dist 2.44, ctrl 3.48, margin +1.04 (n=12, 10 seeds, 2026-06-07) | rework-2: commit footprint fix + experiment harness passed all criteria |
| G2 | PASS | pass-rate 1.0 (10/10 seeds, 2026-06-07) | multi-blocker repair fix + density caps; all traverse ok + density in band |
| G3 | WEAK_PASS | best obj-dist 1.847, ctrl 2.607, margin +0.76 (n=10, 2026-06-07) | beats control by ≥0.5 but <1.0 for PASS; adjacency-graph improvements needed |

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
- 2026-06-07: G3 WEAK_PASS. Reconstruction (All for One): best obj-dist 1.847 tiles
  (≤2.5 ✓), control 2.607 tiles, margin +0.76 (≥0.5 ✓ but <1.0 for PASS). G2 density/placement
  changes tightened the shuffled control (from stale 3.43 to current 2.607); generated
  map improved from FAIL baseline (~3.01) to 1.847. Anti-shortcut: adjacency.json has
  1272 profiles, 14147 neighbours; fit() calls realize() from scratch. 39/39 pytest pass.
  To reach PASS: margin gap of ~0.24 tiles requires tightening guard↔mine coupling in
  adjacency graph. Spec: g3_reconstruct.md. Harness: g3_reconstruct.py. Accepted/advance.
