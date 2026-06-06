---
name: vcmi-mapgen-never-constraints
description: "The hard NEVER constraints for HRNet research — banned patterns (auto-fail), the zero-weights test, and the mandatory pre-close gates. Load for any experiment implementation or review; these override convenience."
---

# HRNet — NEVER Constraints (hard, non-negotiable)

The point of this research is to prove the *trainable network* learns to use
injected knowledge — not that a hand-wired shortcut produces the answer. If a
shortcut would make the experiment pass faster, **that is the failure mode.
Report it; do not use it.**

## NEVER — regardless of framing or apparent simplicity

- No oracle at inference: no `target_code`, gold values, or teacher-forced
  content in the generation/eval path. The model navigates from the input
  embedding alone.
- No keyword pattern-matching on the spec (`if "..." in spec`), no dispatch tables.
- No templates, lookup tables, or hardcoded output strings in the generation path.
- No external classifiers (no `sklearn`, no `classify_intent`, no intent routing).
- Frozen/pre-aligned blocks are **weight matrices**, not caches, lookup tables, or
  retrieval databases.
- No silent reframing toward a recognized architecture (transformer, RAG, neural
  program synthesis) to dodge the hypothesis being tested.

## BANNED PATTERNS — grep before closing; any hit auto-fails

```text
if "..." in spec
return "..."          # hardcoded string in a generate()/render path
handlers[
templates[
outputs[hash(
target_code
except:.*return.*template
sklearn
classify_intent
```

Run the grep over the experiment's code path as part of self-review and as part
of gate-check. A hit is a FAIL, not a warning.

## The zero-weights test (mandatory)

Zeroing the model's trainable weights MUST change the output to garbage. If the
output is unchanged when weights are zeroed, the result is being produced by
heuristics/shortcuts outside the network — FAIL.

```python
with torch.no_grad():
    for p in model.parameters():
        if p.requires_grad:
            p.zero_()
# re-run forward → output must now be degenerate / not the correct answer
```

This is also an anti-shortcut flag (`lookup_flag` / `oracle_route_flag`): if the
answer survives weight-zeroing, a lookup or oracle is present.

## BEFORE CLOSING ANY EXPERIMENT — all three or do not close

```text
[ ] Loss decreased from the training baseline
[ ] Zeroed weights produce different (garbage) output
[ ] All failed artifacts deleted — no commented-out code, no archived fallbacks,
    no dead imports (deletion is truth; the codebase contains only what works)
```

## On failure — autonomous iteration (do not stall, do not invent)

1. State the failure clearly. 2. List ALL plausible root causes. 3. Fix the
first cause. 4. Verify. 5. Pass → close. 6. Fail → next cause. 7. Repeat until
causes exhausted. 8. All fail → report with full diagnosis; **do not invent a new
approach** mid-experiment. Do not ask permission between iterations.
