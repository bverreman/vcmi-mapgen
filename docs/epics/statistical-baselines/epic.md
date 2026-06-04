# Epic: Statistical Baselines & Contrastive Analysis

## Roadmap Source

MVP roadmap: Epic 2, "Statistical Baselines & Contrastive Analysis" in `docs/roadmaps/mvp.md`.

## Purpose

Measure, from the 159 human maps, the learned tables that drive every generator — object composition (marginals), terrain affinity (unary), and pairwise lift (attract/repel) — and quantify exactly how the VCMI RMG's output differs, so generation has concrete targets rather than guesses.

## Outcome

A set of inspectable tables and a contrastive report: per-purpose object density per 1000 tiles (with corpus variance, not just the mean); per-type allowed-terrain sets; a pairwise lift matrix over distance bins showing which objects attract, repel, or are terrain-coupled; and a human-vs-RMG over/underuse table that names the gaps.

## Current State

Marginals and the human-vs-RMG contrastive table exist (`src/stats.py`, `src/stats2.py`, `src/compare1.py`): decoration is 73.7% of objects; RMG overuses BANK 3.3× and REWARD_PICKUP 2.2×, underuses TERRAIN_MODIFIER/SPECIAL (0×), QUEST_GATE 0.2×, TOWN 0.31×, and DECORATION 0.48×; portal *count* is ~equal, so the portal problem is structural, not marginal. Terrain affinity (unary) is implemented in `src/harvest.py` (per-type allowed terrains at ≥15% share). The **pairwise lift matrix is not yet built** — it is the missing core term.

## Scope

- Purpose-budget marginals per object type, with per-map variance/distribution (capture bimodality, e.g. portals).
- Terrain affinity tables: per-type allowed-terrain set, learned by aggregating terrain-under-anchor across all occurrences.
- Pairwise lift matrix: observed co-occurrence ÷ independence, by object-type pair and distance bin; positive = attract, negative = repel.
- Contrastive human-vs-RMG report across all purposes (over/underuse ratios), with the small-sample caveat noted.

## Out of Scope

- Generation itself (Epics 3–5 consume these tables).
- A learned neural critic (Epic 6).
- Object subtype-level lift where corpus counts are too sparse to estimate.

## Key Decisions

- Affinity is learned from aggregated occurrences with a share threshold, not single-anchor bucketing (single-anchor caused ice-on-desert).
- Lift is stored separately from marginals so frequency (how many) and arrangement (where, relative to what) never get conflated.
- RMG is treated as the contrastive negative; its absolute magnitudes are directional only (4 maps).

## Story Breakdown

- **Story name**: Purpose-Budget Marginals With Variance
  **Short goal**: Density per type per 1000 tiles, plus the corpus spread.
  **Primary value**: The composition target for generation.
  **Dependencies**: Corpus + ontology.
  **Suggested story file name**: `01-purpose-marginals.md`
- **Story name**: Terrain Affinity Tables
  **Short goal**: Per-type allowed-terrain sets from aggregated occurrences.
  **Primary value**: Stops cross-terrain placement (the unary term).
  **Dependencies**: Corpus + ontology.
  **Suggested story file name**: `02-terrain-affinity.md`
- **Story name**: Pairwise Lift Matrix
  **Short goal**: Attract/repel by type-pair × distance bin.
  **Primary value**: The term that makes clustering, guarding, and spacing real.
  **Dependencies**: Corpus + ontology + affinity.
  **Suggested story file name**: `03-pairwise-lift-matrix.md`
- **Story name**: Human-vs-RMG Contrastive Report
  **Short goal**: Over/underuse per purpose, with caveats.
  **Primary value**: Names exactly what to fix.
  **Dependencies**: Marginals; RMG `.vmap` reader.
  **Suggested story file name**: `04-contrastive-report.md`

## Acceptance Criteria

- [x] Per-purpose marginals computed over all 159 maps.
- [x] Per-type allowed-terrain sets computed; cross-terrain placement structurally impossible in generation pools.
- [ ] Pairwise lift matrix computed and inspectable; known relationships visible as numbers (pine↔pine attract, monster↔treasure attract, mine↔mine repel at short range).
- [x] Human-vs-RMG over/underuse table produced with the small-sample caveat documented.

## Dependencies

Epic 1 (corpus, ontology, vmap reader). Output tables consumed by Epics 3 and 5.

## Risks

- Lift estimation sparse for rare type-pairs → mitigate with back-off (type→purpose) and minimum-count thresholds; log what is dropped.
- RMG small sample → never report RMG magnitudes without the caveat; widen the RMG corpus if a stronger contrast is needed.

## Verification Strategy

Re-run the stats scripts and confirm tables match the documented findings. For lift, hand-check a few known pairs against intuition and against direct counts. For the contrastive report, confirm the over/underuse signs are stable across the 4 RMG maps.

## Notes for AI Agents

Keep frequency and arrangement separate at all times. Affinity and lift are the same engine that drives both decoration (Epic 3) and gameplay (Epic 5) — build them once, reuse them.
