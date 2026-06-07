# G3 — Reconstruction: seed reproduces "All for One" (spec)

Gate doc: `research/G3_reconstruct.md`

## Hypothesis

After the G1 footprint fix (visitable-tile adjacency profiles), the generator's
spatial object-distance to "All for One" drops below the shuffled control and
below the 2.5-tile PASS bar. The hypothesis is that the learned graph now encodes
guard-mine coupling and benefit-town proximity strongly enough to beat random
placement on this specific target.

Expected: best_objdist ≤ 2.5 tiles over 10 seeds, beating shuffled control by
≥ 1.0 tile.

## Success thresholds (all required for PASS)

| Criterion                     | Requirement                    |
|-------------------------------|--------------------------------|
| best_objdist                  | ≤ 2.5 tiles                   |
| control margin (ctrl − best)  | ≥ 1.0 tile                    |
| seeds searched                | ≥ 10                          |

WEAK_PASS: best_objdist ≤ 3.0 AND margin ≥ 0.5 (at least 10 seeds).
FAIL otherwise.

## Anti-shortcut flags

- Positions are generated from scratch via `deps_realize.realize()`; no
  coordinates are copied from the target .h3m.
- Only statistical target params are used as knobs: density per purpose,
  biome weights, water fraction, structural params, and the spatial
  signature (median nearest-neighbour distances — not tile positions).
- The adjacency graph (adjacency.json) must have non-trivial profiles
  (loaded and active during placement).

## Measurements (10 seeds, post-G2 code)

| Metric             | Value | Threshold | Status    |
|--------------------|-------|-----------|-----------|
| seeds_searched     | 10    | ≥ 10      | PASS      |
| best_objdist       | 1.847 | ≤ 2.5     | PASS      |
| control_objdist    | 2.607 | —         | —         |
| control_margin     | 0.760 | ≥ 1.0     | FAIL      |

Verdict: **WEAK_PASS**. The G1 footprint fix brought best_objdist from ~3.0 to
1.847, well inside the bar. However the shuffled control also tightened after
the G2 density/placement changes (from ~2.67 to 2.61), leaving a margin of 0.76
which clears the WEAK_PASS bar (≥ 0.5) but not the PASS bar (≥ 1.0).

Note: The pre-G2 benchmark.json stored control_objdist=3.43 (from an earlier
code state); that value is stale. The authoritative value is from the current
harness run above.
