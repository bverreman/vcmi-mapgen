# Epic: Validation & Quality Harness

## Roadmap Source

MVP roadmap: Epic 6, "Validation & Quality Harness" in `docs/roadmaps/mvp.md`.

## Purpose

Judge "human-ness" of generated maps, since visual inspection in the VCMI editor is the only ground-truth quality signal. Provide fast feedback at every generation milestone: a schematic render, an editor round-trip, a statistical fingerprint comparison, and ultimately a learned discriminator trained on human-vs-generated.

## Outcome

Any generated map can be (a) rendered schematically, (b) confirmed to open in the editor, (c) scored against the human statistical fingerprint (marginals, affinity, lift, macro metrics) with named deviations, and (d) ranked by a contrastive discriminator that learns where generated maps still differ from human ones — feeding the next round of work.

## Current State

The schematic renderer and the editor-openable `.vmap` writer exist and are validated. The statistical fingerprint comparison is partial (the human-vs-RMG contrastive report exists; a generated-vs-human version does not). No learned discriminator yet.

## Scope

- Schematic renderer (done): terrain + purpose-coloured objects, multi-level.
- Editor round-trip validation: confirm generated `.vmap` loads (structural reload + manual editor open).
- Statistical fingerprint critic: compare a generated map's marginals/affinity/lift/macro-metrics to the human corpus and report named deviations and an aggregate score.
- Contrastive discriminator: train a classifier on local patches / map features to separate human from generated; use it as a "human-ness" score and to surface the most obvious current tells. Iterate (add generations as negatives) to peel off tells.

## Out of Scope

- Gameplay balance evaluation.
- Automated headless editor screenshotting (the flatpak editor does not run reliably headless; rely on the schematic renderer for agent-side inspection and manual editor opens for ground truth).

## Key Decisions

- The schematic renderer is the agent's primary inspection tool; the editor is the human's ground truth.
- The critic operates on the same learned tables as generation, so a high score means the generator reproduced the targeted statistics.
- The discriminator's contrast is against the current generator and the RMG; tells it cannot see (shared by both distributions) need the human eye.

## Story Breakdown

- **Story name**: Schematic Renderer
  **Short goal**: Render any map to a purpose-coloured PNG.
  **Primary value**: Agent-side inspection.
  **Dependencies**: Epic 1.
  **Suggested story file name**: `01-schematic-renderer.md`
- **Story name**: Editor Round-Trip Validation
  **Short goal**: Confirm generated `.vmap` opens cleanly.
  **Primary value**: No dead artifacts.
  **Dependencies**: Epic 1 writer.
  **Suggested story file name**: `02-editor-round-trip.md`
- **Story name**: Statistical Fingerprint Critic
  **Short goal**: Score generated vs human distributions; name deviations.
  **Primary value**: Quantitative "how human" feedback.
  **Dependencies**: Epic 2 tables.
  **Suggested story file name**: `03-statistical-critic.md`
- **Story name**: Contrastive Discriminator
  **Short goal**: Learn human-vs-generated; rank and surface tells.
  **Primary value**: Finds gaps the marginals miss.
  **Dependencies**: Generated corpus; human corpus.
  **Suggested story file name**: `04-contrastive-discriminator.md`

## Acceptance Criteria

- [x] Any map renders to a schematic PNG.
- [ ] Every generated `.vmap` is confirmed to open in the VCMI editor (manual or scripted reload).
- [ ] The statistical critic reports per-axis deviations and an aggregate human-ness score for a generated map.
- [ ] The discriminator produces a human-vs-generated score and a ranked list of current tells.

## Dependencies

Epic 1 (render/write), Epic 2 (tables). Judges Epics 3–5.

## Risks

- Over-fitting the critic to the targeted statistics (a generator can match them and still look wrong) → keep the human eye in the loop; use the discriminator to find unmodeled tells.
- Headless editor unavailability → accept manual editor opens for ground truth; do not block on automation.

## Verification Strategy

Render and round-trip every generated milestone. Track the fingerprint score and discriminator score across iterations; both should trend toward human as Epics 3–5 land. Periodically open generated maps in the editor and record qualitative notes.

## Notes for AI Agents

A good statistical score is necessary, not sufficient. Always pair it with a render and, at milestones, a real editor open. When the discriminator finds a tell, that tell becomes the next story.
