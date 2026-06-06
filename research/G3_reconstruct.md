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
