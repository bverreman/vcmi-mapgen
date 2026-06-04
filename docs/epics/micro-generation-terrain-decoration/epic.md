# Epic: Micro Generation — Terrain & Decoration

## Roadmap Source

MVP roadmap: Epic 3, "Micro Generation: Terrain & Decoration" in `docs/roadmaps/mvp.md`.

## Purpose

Generate the texture layer — terrain and decoration — so that it matches human maps' local feel: realistic terrain transitions, correct per-terrain decoration density, terrain-appropriate decoration, and decoration that **clusters** (forests, rock fields) rather than scattering uniformly.

## Outcome

A generator that produces an editor-openable terrain layer with decoration that is (a) on the right terrain, (b) at human density (~240–250 decoration objects per 1000 land tiles), and (c) clustered via the learned pairwise lift so groves and clearings emerge instead of uniform peppering. Roads render as proper overlay tiles on top of terrain.

## Current State

Terrain uses a macro biome scaffold (noise-Voronoi, controlled proportions) textured by a learned 4-neighbour Markov "edge blend" — this avoids both the raster-Markov diagonal streaking and the Gibbs lava-drift discovered during spike work. Decoration is affinity-gated (correct terrain, the unary term is done). **Clustering is still a cosmetic noise field, not the learned pairwise lift** — this is the main remaining gap. Roads are a dirt-carve placeholder; they must become real overlay tiles.

## Scope

- Terrain generator: macro biome scaffold + Markov edge-texture; correct marginal proportions; no directional streaking or terrain drift.
- Decoration affinity gate (done): only place a type on terrains in its learned allowed set.
- Decoration density matching: hit the per-terrain human density target.
- Decoration clustering via pairwise lift: Gibbs/sequential sampling so same-type decoration attracts into contiguous clumps.
- Roads as overlay tiles: write the VCMI road overlay into the tile string, not a terrain-type change.
- Terrain edge-view selection so biome borders render with transition sprites, not hard seams.

## Out of Scope

- Visitable/gameplay objects (Epic 5) — micro generation places terrain and decoration only.
- Region layout, town placement, connectivity topology (Epic 4).
- Rivers as a learned feature (defer unless cheap).

## Key Decisions

- Architecture is macro scaffold + micro Markov, **not** pure Markov (proven necessary: pure local terrain streaks or drifts).
- Decoration affinity is a hard gate sourced from Epic 2's per-type allowed-terrain sets.
- Clustering must be emergent from the lift term, not imposed by a hand-tuned noise field.
- Roads are an overlay layer (like rivers); the encoding comes from VCMI terrain config since RMG maps contain no roads to copy.

## Story Breakdown

- **Story name**: Terrain Generator (Macro Scaffold + Markov Texture)
  **Short goal**: Isotropic biomes, right proportions, realistic borders.
  **Primary value**: A believable terrain base.
  **Dependencies**: Epic 2 terrain Markov tables.
  **Suggested story file name**: `01-terrain-generator.md`
- **Story name**: Decoration Affinity Gate
  **Short goal**: Place decoration only on its allowed terrains.
  **Primary value**: Kills ice-on-desert.
  **Dependencies**: Epic 2 affinity tables.
  **Suggested story file name**: `02-decoration-affinity-gate.md`
- **Story name**: Lift-Driven Decoration Clustering
  **Short goal**: Decoration clumps via Gibbs sampling on the pairwise lift.
  **Primary value**: Forests instead of static.
  **Dependencies**: Epic 2 pairwise lift matrix.
  **Suggested story file name**: `03-lift-driven-decoration-clustering.md`
- **Story name**: Roads as Overlay Tiles
  **Short goal**: Encode roads on top of terrain, not as dirt.
  **Primary value**: Correct, real-looking roads.
  **Dependencies**: VCMI road-overlay encoding.
  **Suggested story file name**: `04-roads-as-overlay-tiles.md`
- **Story name**: Terrain Edge-View Selection
  **Short goal**: Pick transition tile views at biome borders.
  **Primary value**: Smooth coastlines/edges, not hard seams.
  **Dependencies**: Terrain generator; VCMI tile-view rules.
  **Suggested story file name**: `05-terrain-edge-views.md`

## Acceptance Criteria

- [x] Generated terrain is isotropic (no diagonal streaking) and keeps human-like terrain proportions.
- [x] Every decoration object sits on a terrain in its learned allowed set (no cross-terrain placement).
- [ ] Decoration density per terrain is within tolerance of the human target.
- [ ] Same-type decoration forms visible contiguous clumps emergent from the lift term, not a noise field.
- [ ] Roads appear as overlay tiles in the editor, on top of unchanged terrain.
- [ ] Output `.vmap` opens in the VCMI editor and reads as organic terrain on visual inspection.

## Dependencies

Epic 1 (writer, renderer), Epic 2 (terrain Markov, affinity, lift). Feeds Epic 6 validation.

## Risks

- Gibbs drift/collapse on decoration as on terrain → constrain moves to locally-present types and cap sweeps.
- Wrong road-overlay encoding → validate by opening in the editor; do not assume the string format.
- Footprint overlap at high density → occupancy check on object masks.

## Verification Strategy

Render generated terrain and compare to a real map of the same size; confirm no streaking and correct proportions. Statistically confirm zero cross-terrain decoration and density within tolerance. Visually confirm clumping. Open the `.vmap` in the editor and confirm roads and decoration look right.

## Notes for AI Agents

The recurring failure mode here is faking a learned term with a heuristic (noise-field "clustering"). If a term can be measured from the corpus (Epic 2), measure it and sample from it. Always emit both a render and an editor-openable `.vmap`.
