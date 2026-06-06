---
name: vcmi-mapgen-scientific-method-controls
description: "The scientific discipline for HRNet research — control-relative claims, the four shared controls, shared metrics, PASS/WEAK_PASS/FAIL gate semantics, and program kill criteria. Load when planning, implementing, or checking any experiment."
---

# HRNet — Scientific Method & Controls

The program tests one thing: **does pre-aligning/segmenting a network reduce
training cost, and does that survive generalization?** Every result is a *delta
against a control*. "Loss went down" is not a result. "Reached capability X in
0.4× the steps of scratch while beating shuffled, over seeds {0..4}" is.

## The four shared controls (instantiate all from one spec)

For any pre-aligned/segmented model `M`, run the identical task/budget against:

```text
scratch    — same architecture, random init, no pre-alignment
random     — pre-aligned slots filled with random vectors of matched norm/spectrum
shuffled   — correct pre-aligned content, assigned to the WRONG slots/keys
same-param — a dense model with the same trainable+active param budget as M
```

A pre-alignment result is **meaningful only if M beats `random` AND `shuffled`**
(it used the injected *content*, not just added capacity). It is *interesting*
only if it also beats or ties `scratch`/`same-param` on the cost axis claimed.

## Shared metrics (report all, scored from raw output)

```text
steps_to_threshold      walltime_to_threshold (reported, not gated)
data_to_threshold       trainable_params / active_params / loaded_params
final_capability        generalization_gap (train-symbol vs held-out-symbol)
```

Capability is always measured from raw model output — never from a repair,
compiler, template, or lookup (see `hrnet-never-constraints`).

## Gate verdicts

Each gate doc (`specs/hrnet/research/P*.md`) states its own exact thresholds.
Map measured results to:

- **PASS** — meets the gate's full criterion (e.g. P1: `steps ≤ 0.6×scratch` AND
  `< random` AND `< shuffled`, all anti-shortcut flags false).
- **WEAK_PASS** — beats `random`/`shuffled` (content carries signal) but only
  ties `scratch`/`same-param`. Recorded and the program **advances** (does not
  loop forever) — but the weakness is logged.
- **FAIL** — does not beat `random`/`shuffled`, OR an anti-shortcut flag is true.

## Program kill criteria (stop after P0–P1 if any hold)

```text
- Pre-aligned init does not beat random/shuffled at equal budget
  (pre-alignment carries no usable signal).
- It only helps when the task is fully known in advance (k=1.0)
  — it has collapsed into a lookup table (forbidden; see never-constraints).
- It helps train-symbol capability but generalization_gap is no better
  than scratch (it memorized, did not align).
```

When a kill criterion fires, the verdict is **FAIL** and the outcome is a
recorded **negative result** under `specs/hrnet/findings/` — never deleted, never
softened. A clean negative result is a real contribution.

## The "known fraction" knob `k`

Experiments parameterize how much of the mapping is revealed to pre-alignment
(`k`). `k=1.0` is the forbidden lookup regime (upper bound only); `k=0.0` is pure
scratch; the science lives at `0 < k < 1`. Never report a `k=1.0` number as a
capability result.
