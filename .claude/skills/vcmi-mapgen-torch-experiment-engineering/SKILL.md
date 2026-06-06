---
name: vcmi-mapgen-torch-experiment-engineering
description: "PyTorch experiment engineering for HRNet research — determinism, device/dtype, no-NaN, torch 2.2 API ceiling. Load when writing or editing any experiment code under src/hrnet."
---

# HRNet — PyTorch Experiment Engineering

Every HRNet experiment is a measurement instrument. If it is not deterministic
and shape-checked, its numbers cannot be compared against controls and the result
is void. Follow these rules in every experiment module and test.

## Determinism (mandatory)

Seed everything at the top of every run, before any tensor is created:

```python
import os, random
import numpy as np
import torch

def seed_everything(seed: int) -> None:
    os.environ["PYTHONHASHSEED"] = str(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
```

- Same seed → **bitwise-identical** step/data curves. A test should assert this.
- Never call `Math.random`-style nondeterminism, ungoverned `torch.rand` without
  the seeded generator, or data shuffling without a seeded `Generator`.
- `walltime_to_threshold` is inherently nondeterministic — it is a *reported*
  metric, never a reproducibility-gated one. Gate reproducibility on step/data
  curves only (see `hrnet-reproducibility-multiseed`).

## Device & dtype

- Default to **CPU, float32** for the toy/measurement tasks. These experiments
  must run on consumer hardware and in CI without a GPU.
- Plumb `device` and `dtype` explicitly; never rely on a global default.
- The existing modules construct tensors as float32 and compute cosine/MSE in
  float (`src/hrnet/vector.py`). Match that — do not silently introduce float64
  except inside a `torch.linalg.pinv`/`lstsq` solve (which the repo already does
  in `linear.py`), then cast back to float32.

## Shape & finiteness discipline

Every `forward` and every metric computation asserts before returning:

```python
assert x.shape == (D, D), f"expected {(D, D)}, got {tuple(x.shape)}"
assert torch.isfinite(x).all(), "non-finite values in output"
```

- The HRNet modules already raise `ValueError` on shape mismatch
  (`hrnet_loop.py`, `page_select.py`). Keep that contract; do not relax it.
- A NaN/Inf anywhere is a failed experiment, not a warning. Surface it.

## Torch version ceiling

- The repo pins `torch >=2.2,<2.3`. Do **not** use APIs introduced after 2.2
  (e.g. newer `torch.compile` options, post-2.2 `torch.func` surface). When in
  doubt, prefer plain tensor ops that have existed since 2.0.
- Reuse existing primitives instead of reimplementing: `solve_linear` /
  `least_squares_linear` (`src/hrnet/linear.py`), token builders and `cosine` /
  `normalize` (`src/hrnet/vector.py`).

## Performance is not the point — correctness is

These are small experiments (D=64 toys). Do not optimize with fused kernels,
half precision, or batching tricks that change numerics. A readable, exactly
reproducible loop beats a fast one.
