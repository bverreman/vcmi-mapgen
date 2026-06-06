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

## Result — PASS (2026-06-06)

Reviewer re-ran `uv run python src/benchmark.py --maps 8 --seeds 4` independently.

| Criterion | Expected | Observed |
|-----------|----------|----------|
| Exits 0, runs to completion | exit 0 | exit 0 ✓ |
| Output contains all 3 scores + control_objdist | present | present ✓ |
| Determinism (two runs identical) | exact match | mean_objdist 3.66, pass_rate 0.0 reproduced exactly ✓ |
| Shuffled control uses same object multiset | same objects, random positions | confirmed ✓ |

Anti-shortcut flags: lookup=false, oracle_route=false, repair=false, leak=false.
No map special-casing found in benchmark.py, deps_fit.py, or deps_realize.py.
`reachability_repair` does not inflate scores (pass_rate 0.0 with it active).
pytest 10/10 passed.

**Advance to G1.**
