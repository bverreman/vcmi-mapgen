# G3 — Reconstruction: a seed reproduces a named real map

**Question.** Can a single seed (+ params from the target) reproduce a *specific*
hand-made map — "All for One" — closely, and beat the shuffled control?

**Experiment.** `benchmark.py` RECONSTRUCT: `--target "All for One"`, search seeds,
report best object-distance + total and the shuffled control.

## Exact success gate (all required)

- **best object-distance ≤ 2.5 tiles** to the target.
- **Control-relative:** best object-distance ≤ shuffled-control − **1.0 tile**.
- total fit-distance is reported (terrain + density + struct + object-distance) for
  context; not itself gated until G1/G2 pass.

PASS if both hold; WEAK_PASS if object-distance ≤ 3.0 and beats control by ≥ 0.5;
FAIL otherwise.

## Note on the target name

The objective named "One for All". The corpus has **"All for One"** (2-level) and
**"And One for All"** (1-level). Default target is "All for One"; if the operator
meant the other, set `--target "And One for All"` and update this doc.

## Known starting point (2026-06)

best object-distance ~3.0 vs control ~2.7 — the control currently BEATS the graph,
so **FAIL**. This is the same root cause as G1 (lost gameplay couplings); G3 depends
on G1.

## Anti-shortcut

- The generated map is produced from scratch by the generator; positions are not
  copied from the target. Only target-derived params (density, biomes, signature)
  may be used as knobs.

## Result (2026-06-07)

**Verdict:** WEAK_PASS

- **best object-distance:** 1.847 tiles (seed=1 of 10) vs threshold ≤ 2.5 ✓
- **control object-distance:** 2.607 tiles
- **margin:** 0.760 tiles vs threshold ≥ 0.5 ✓ (WEAK_PASS; < 1.0 for PASS)
- **Metrics:** best_objdist=1.847 ≤ 2.5, control=2.607, margin=+0.76 (clears WEAK_PASS ≥0.5, misses PASS ≥1.0)
- **Anti-shortcut status:** adjacency.json non-trivial (1272 profiles, 14147 neighbours); fit() calls realize() from scratch with sig_target knob; zero-weights check passes; 39/39 pytest tests pass.
- **Notes:** G2 density/placement changes tightened the shuffled control from stale benchmark.json (3.43) to current (2.607); generated map improved from FAIL baseline (~3.01) to 1.847. To reach PASS, margin gap of ~0.24 tiles needs closing—likely requires further tightening of guard↔mine coupling in adjacency graph.
