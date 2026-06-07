# G2 — Validity: generated maps make sense

**Question.** Without a target, does a plain `realize(seed)` produce a map that is
playable and faithful — every object reachable, and the object distribution within
corpus tolerance?

**Experiment.** `benchmark.py` VALIDITY: for K seeds, generate, then check
reachability (`traverse.py`) and per-purpose density within [0.5, 2.0]x the corpus
rate.

## Exact success gate (all required)

- K ≥ 10 seeds.
- **pass-rate ≥ 0.9**, where a seed passes iff:
  - `traverse.traverse(fm, em)["ok"]` is True (every zone, town and mine reachable on
    foot from the start town, both levels), AND
  - every purpose with corpus rate ≥ 0.2/1k is within [0.5, 2.0]x that rate.
- Each passing seed also loads in the editor (`gate.load_test`, no fatal) — checked
  by the reviewer on a sample.

## Controls

- histogram-only (random scatter, no graph): expected to FAIL the density or
  reachability checks less often — used to confirm the gate discriminates, not that
  the graph is required for validity.

## Known starting point (2026-06)

pass-rate 0.0 — failing reachability and/or density tolerance after the immediate-
touch rework. Diagnose which (the rows in `out/benchmark.json` list `reachable` and
`density_off` per seed) and fix.

## Anti-shortcut

- Reachability must be the real BFS over passable land + chokepoints, not a proxy.
- Density tolerance is the fixed [0.5, 2.0]x band; do not widen it to pass.

## Result — PASS (2026-06-07)

Independent reviewer re-ran `g2_validity.py --seeds 10`: pass-rate 1.0 (10/10 seeds).

| Criterion | Requirement | Observed |
|-----------|-------------|----------|
| Pass-rate | ≥ 0.9 | 1.0 ✓ |
| Seeds | ≥ 10 | 10 ✓ |
| Reachability | All zones, towns, mines reachable from start | true for all 10 ✓ |
| Density tolerance | within [0.5, 2.0]x corpus rate | no outliers (density_off=[]) ✓ |

Anti-shortcut flags: all false. `traverse.py` is real 4-connected BFS from start town through subterranean gates; no proxy. `reachability_repair` uses Dijkstra + `all_removable` set to correctly clear all overlapping decoration blockers per tile. Adjacency weights are corpus-learned share×deg Poisson parameters (non-zero, real). Density band [0.5, 2.0]x is fixed, not widened. Editor load-test sampled (pytest 26/26).

**Advance to G3.**
