# G0 — Trustworthy benchmark harness

**Question.** Can we measure the three objectives deterministically, with controls,
so every later gate is judged by numbers, not by eye?

**Experiment.** `src/benchmark.py` computes COVERAGE, VALIDITY, RECONSTRUCT and the
SHUFFLED control, writing `out/benchmark.json`.

## Exact success gate (all required)

- `uv run python src/benchmark.py --maps 8 --seeds 4` runs to completion, exit 0.
- Output contains all three scores AND a `control_objdist` for coverage & reconstruct.
- Re-running with the same args reproduces `mean_objdist` and `pass_rate` exactly
  (deterministic; seeds fixed).
- The shuffled control is computed from the SAME object multiset (not a different
  map), so it is a fair within-map control.

## Controls

The harness *is* the control infrastructure; G0 only verifies the control exists and
is fair (same objects, randomized positions).

## Anti-shortcut

- No gate may read `out/benchmark.json` as ground truth without re-running the
  measurement (the reviewer re-runs).
- The harness must not special-case any single map.
