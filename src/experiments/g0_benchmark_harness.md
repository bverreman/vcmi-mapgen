# G0 — Trustworthy benchmark harness (spec)

Gate doc: `research/G0_benchmark_harness.md`

## Hypothesis

The existing `src/benchmark.py` can serve as a trustworthy fitness function for all
later gates: it runs to completion, its output contains the required scores and
controls, its primary metrics are reproducible across runs, and its shuffled control
is a fair within-map comparison (same object multiset, randomised positions only).

## Success thresholds (all required)

| Criterion | Requirement |
|-----------|-------------|
| Exit code | 0 |
| Output fields | `coverage.mean_objdist`, `coverage.mean_control_objdist`, `coverage.pct_within_bar`, `validity.pass_rate`, `reconstruct.best_objdist`, `reconstruct.control_objdist` all present |
| Determinism | `mean_objdist` and `pass_rate` identical across two independent runs with the same `--maps` / `--seeds` args |
| Fair control | `_shuffled_objdist` builds pts exclusively from `fm["objects"]` of the target generated map; the type multiset is unchanged, only positions differ |

## Design

This is a **harness verification**, not a generative-model experiment. No neural
network is trained. The experiment script:

1. Invokes `benchmark.coverage()`, `benchmark.validity()`, and
   `benchmark.reconstruct()` programmatically (same logic as the CLI) at the scale
   required by the gate (`--maps 8 --seeds 4`).
2. Runs the benchmark twice and asserts `mean_objdist` and `pass_rate` are equal.
3. Confirms the JSON output schema matches the required fields.
4. Reports measured values.

## Fix applied

`_shuffled_objdist` originally used the unseeded global `random.choice`, making
`mean_control_objdist` non-deterministic across interpreter invocations. Fixed by
deriving a deterministic seed from the object-type tuple of the generated map, then
using a local `random.Random(seed)` instance. The type multiset and free-tile pool
are unchanged — only the RNG source is local and seeded.

## Metrics to report

- `mean_objdist` (coverage) — mean±std over two determinism runs
- `mean_control_objdist` (coverage) — value after fix
- `pass_rate` (validity)
- `best_objdist` (reconstruct)
- `control_objdist` (reconstruct)
- determinism_ok: bool
