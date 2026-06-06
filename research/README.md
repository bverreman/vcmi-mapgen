# VCMI map-generator research program

Goal: a generator whose distribution **contains** the hand-made maps — provable by
objective criteria, not by eye. The gate ladder turns the three research questions
into falsifiable, control-relative gates. The autonomous engine works the lowest
not-yet-PASS gate, implements, has an independent reviewer re-run the measurement,
reworks on failure, and records.

The fitness function for every gate is `python3 src/benchmark.py` (writes
`out/benchmark.json`). Claims must be **control-relative**: the graph generator
must beat the SHUFFLED control (same objects, random positions) — improving in
absolute terms is not enough.

## Gate ladder

| Gate | Question | Doc | Depends on |
|------|----------|-----|------------|
| G0 | Is the benchmark trustworthy (deterministic, has controls)? | [G0_benchmark_harness.md](G0_benchmark_harness.md) | — |
| G1 | Coverage: do the hand-made maps lie in the distribution? | [G1_coverage.md](G1_coverage.md) | G0 |
| G2 | Validity: do generated maps make sense (reachable + distribution respected)? | [G2_validity.md](G2_validity.md) | G0 |
| G3 | Reconstruction: a seed reproduces a named real map ("All for One")? | [G3_reconstruct.md](G3_reconstruct.md) | G0, G1 |

## Controls (shared)

- **shuffled** — same generated objects, positions reassigned to random free tiles.
  The graph generator's object-distance must beat this; if it can't, the placement
  carries no real structure.
- **histogram-only** — density matched, no adjacency graph (random scatter). A gate
  that the graph passes but histogram-only fails proves the graph is load-bearing.

## Program kill criteria

- **Method-is-noise kill:** if, after the rework budget, the graph generator cannot
  beat the shuffled control by ≥ 1.0 tile of object-distance on G1, the statistical
  approach is killed → research lead decides revive vs new direction.
- **Unfixable validity kill:** if G2 reachability cannot reach ≥ 0.9 pass-rate, the
  macro/realization design is killed → lead review.

## Status

See [PROGRESS.md](PROGRESS.md).
