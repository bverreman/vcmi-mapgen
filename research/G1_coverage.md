# G1 — Coverage: hand-made maps lie in the distribution

**Question.** For a real map, can the generator be dialled (params from the target,
seed searched) to produce a map statistically close to it — and is that closeness
real (beats the shuffled control)?

**Experiment.** `benchmark.py` COVERAGE: fit each of N corpus maps, record
object-distance (spatial nearest-neighbour signature distance), total distance, and
the shuffled-control object-distance.

## Exact success gate (all required)

- N ≥ 12 corpus maps (evenly-spaced slice).
- **mean object-distance ≤ 2.5 tiles.**
- **≥ 80% of maps have object-distance ≤ 3.0.**
- **Control-relative:** mean object-distance ≤ mean shuffled-control − **1.0 tile**
  (the placement must be meaningfully better than random).

Classify: PASS if all four hold; WEAK_PASS if the first three hold but the control
margin is in [0.5, 1.0); FAIL otherwise.

## Controls

- shuffled (built into benchmark). The graph must beat it by ≥ 1.0 tile.

## Known starting point (2026-06)

Immediate-touch adjacency rework regressed gameplay object-distance to ~3.0, barely
above the shuffled control (~3.3) — i.e. currently **FAIL**. Suspected cause: the
mine→guard / approach-guard couplings sit ~2 tiles off the object (not touching), so
the strict touch graph dropped them. Candidate fix: include each object's visitable
(`A`) tile in `deps_adj.footprint()` so approach-guards count as immediate.

## Anti-shortcut

- May not overfit thresholds to one map; the gate is the mean + percentile over N.
- May not tune the metric to flatter the generator; the object-distance definition in
  `deps_spatial.py` is fixed for the program.
