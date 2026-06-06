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
