---
name: vcmi-mapgen-sparsity-aware-param-counting
description: "How to count trainable, loaded, and active (sparsity-aware) parameters for HRNet experiments. Load when implementing the P0 measurement harness or any experiment that claims a param/compute saving."
---

# HRNet — Sparsity-Aware Parameter Counting

The whole program's headline claim is "reaches capability with fewer params /
less active compute." That claim is meaningless unless `active` is measured as
**params actually touched in a forward pass**, not params allocated. This skill
is the canonical implementation referenced by P0 deliverable #1.

## The three counts

```python
def trainable_params(model: torch.nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

def loaded_params(model: torch.nn.Module) -> int:
    # Everything resident in memory: parameters AND buffers (frozen knowledge
    # matrices are buffers in EmbeddedKnowledgeBlock, so they count as loaded).
    return sum(p.numel() for p in model.parameters()) + \
           sum(b.numel() for b in model.buffers())
```

- **trainable** — `requires_grad` only. Frozen/pre-aligned weights are excluded.
- **loaded** — all parameters + buffers resident in memory. Frozen knowledge
  matrices (registered as buffers, e.g. `EmbeddedKnowledgeBlock.knowledge_matrix`)
  ARE loaded params even though they are not trainable.
- **active** — params whose tensor was *read during a specific forward*. For a
  routed/segmented model where only k of E experts fire, active ≪ loaded.

## Measuring active params (the hard one)

Do NOT approximate active by allocation. Measure it. Two acceptable methods:

1. **Forward hooks** — register a forward hook on every leaf module; a module
   that fires contributes its parameter+buffer numel to the active set for that
   input. Deduplicate by module identity.

```python
def active_params(model, *forward_args) -> int:
    fired: set[int] = set()
    handles = []
    def hook(mod, inp, out):
        fired.add(id(mod))
    for m in model.modules():
        if not list(m.children()):  # leaf
            handles.append(m.register_forward_hook(hook))
    model(*forward_args)
    for h in handles: h.remove()
    return sum(
        sum(p.numel() for p in m.parameters(recurse=False)) +
        sum(b.numel() for b in m.buffers(recurse=False))
        for m in model.modules() if id(m) in fired
    )
```

2. **Explicit routing mask** — when the router emits a discrete top-k expert
   selection, sum the param counts of the selected experts plus the always-on
   router/backbone. This is exact for MoE-style segmentation (P3).

A hook-fired module that received the input but multiplied by a **zero gate**
still counts as active under method 1 (it did the matmul). If you want
gate-aware active counting, use method 2 with the actual selection mask and say
so explicitly in the experiment notes — never silently mix the two.

## Worked sanity check (required in the P0 harness test)

Build a 4-expert bank where the router fires exactly 1 expert. Assert:

```text
active_params  ≈  loaded_params / 4   (+ always-on router/backbone)
trainable_params  <=  loaded_params
```

If `active == loaded` on this toy, the counter is wrong (it is counting
allocation, not usage) and the harness must be rejected.

## Reporting

Always report all three counts side by side with the capability number, and
always against the same counts for every control (`scratch/random/shuffled/
same-param`). A saving is a *ratio vs a control*, never an absolute count.
