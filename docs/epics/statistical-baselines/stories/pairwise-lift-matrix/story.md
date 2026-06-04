# Story: Pairwise Lift Matrix

## Epic

Statistical Baselines & Contrastive Analysis: `docs/epics/statistical-baselines/epic.md`

## Implementation Status

- **Status**: To Do
- **Priority**: High (blocks lift-driven clustering and guard↔reward coupling)
- **Plan**: TBD
- **Verification**: Inspect the matrix; hand-check known pairs against direct counts and intuition.

## User / Operator Value

- end result: maps where same-type decoration clumps, guards sit on rewards, and mines space out — because the model knows these relationships as numbers.
- developer / AI agent: one reusable table that drives both decoration clustering (Epic 3) and gameplay placement (Epic 5).

## Context

Object composition (marginals) and terrain affinity (unary) are measured, but the **arrangement** term is not. Arrangement is the pairwise relationship: given object A, how much more or less likely is object B nearby than chance. This is the term that makes clustering, guarding, and spacing real instead of cosmetic. Without it, generation falls back to independent per-tile draws, which the user correctly called "semi-random." The matrix is computed from the 159 maps in `out/maps/*.json` using `ontology.resolve()` for types and the per-cell terrain for context, mirroring `src/harvest.py`'s aggregation style.

## Scope

- Compute lift `L(A, B, d) = observed_cooccurrence(A, B at distance band d) / expected_under_independence`, for object types/purposes A, B over a small set of distance bands (e.g. 0–2, 3–6, 7–15 tiles).
- Store the matrix keyed by `(A, B, band)`; positive (>1) = attract, negative (<1) = repel.
- Provide back-off: when a type-pair is too sparse, fall back to purpose-level lift; log dropped/low-count pairs (no silent truncation).
- Keep frequency (marginals) and arrangement (lift) strictly separate.
- Emit an inspectable artifact (`out/lift.json`) and a human-readable top-attract / top-repel summary.

## Out of Scope

- Using the matrix in a generator (Epic 3 / Epic 5 consume it).
- Subtype-level lift where counts are too sparse.
- Directional/anisotropic lift (distance-only is sufficient for v1).

## Requirements

- The matrix must distinguish attraction from repulsion and must vary by distance band (e.g. mine↔mine repels at 0–2 but co-occurs regionally at 7–15).
- Known relationships must be visible as numbers: pine↔pine attract, monster↔treasure/reward attract, mine↔mine repel at short range, decoration↔matching-terrain affinity.
- Expected-under-independence must use the actual per-terrain opportunity, not a flat map average, so terrain-coupled co-occurrence isn't mistaken for object attraction.
- Sparse pairs must back off to purpose level and be logged, with counts, so coverage is auditable.
- Output must be a stable, documented artifact other modules load without recomputation.

## Open Questions

- Distance bands: fixed (0–2 / 3–6 / 7–15) or learned from the data's correlation length? Default to fixed for v1.
- Lift over object *anchors* vs *footprints* — anchors for v1 (consistent with affinity), revisit if clustering looks off.
