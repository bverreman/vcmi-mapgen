# Story: Lift-Driven Decoration Clustering

## Epic

Micro Generation — Terrain & Decoration: `docs/epics/micro-generation-terrain-decoration/epic.md`

## Implementation Status

- **Status**: To Do (blocked on Pairwise Lift Matrix)
- **Priority**: High
- **Plan**: TBD
- **Verification**: Render generated decoration; confirm visible same-type clumps; confirm density still matches the human target.

## User / Operator Value

- end result: forests and rock fields that clump like real maps, with clearings between — instead of uniform vegetation static.
- developer / AI agent: replaces the cosmetic noise-field "clustering" with emergent structure from the measured lift.

## Context

Decoration currently lands on the right terrain (the affinity gate is done) but is placed by independent per-tile Bernoulli draws modulated by a hand-tuned noise field. That is not learned and not real clustering — the user flagged it as "vegetation placed semi-randomly." Real decoration clusters because same-type objects attract at short range. With the Pairwise Lift Matrix (Epic 2) available, clustering should *emerge* from conditional sampling: place a pine, and its neighbours' pine probability rises, so a grove grows of one coherent type. The current generator is `src/generate3.py` (`scatter_clustered`), which this story replaces.

## Scope

- Replace the noise-field scatter with Gibbs/sequential conditional sampling driven by the pairwise lift matrix and the terrain-affinity gate.
- Conditional placement: `P(place type T at tile | terrain at tile, decoration already near tile)`, combining the unary affinity gate with the pairwise lift over nearby same/other-type decoration.
- Preserve the per-terrain human density target as the marginal the sampler converges to.
- Keep the occupancy/footprint check so multi-tile decoration does not overlap.
- Constrain sampling to avoid the Gibbs failure modes seen on terrain (drift/collapse): restrict candidate types to the affinity set and cap sweeps.

## Out of Scope

- Gameplay/visitable objects (Epic 5 uses the same engine separately).
- Terrain generation (separate story).
- Cross-type decoration relationships beyond what the lift matrix provides.

## Requirements

- Same-type decoration must form visible contiguous clumps that emerge from the lift term, not from an imposed noise field.
- Per-terrain decoration density must remain within tolerance of the human target after clustering.
- No decoration may be placed off its affinity terrain (the unary gate still applies).
- No footprint overlap between placed objects.
- The sampler must not drift to a single dominant type or collapse coverage (cap sweeps; restrict candidates to locally-valid types).
- Output must be an editor-openable `.vmap` plus a schematic render for inspection.

## Open Questions

- Sampling order: raster with look-behind, or full Gibbs sweeps with a random/checkerboard schedule? Prefer a few constrained sweeps; decide empirically by render quality.
- How strongly to weight lift vs the base density so clumps form without starving clearings.
