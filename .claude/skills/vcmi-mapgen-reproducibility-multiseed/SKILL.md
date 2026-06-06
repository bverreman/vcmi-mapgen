---
name: vcmi-mapgen-reproducibility-multiseed
description: "Multi-seed reproducibility discipline for HRNet experiments — ≥5 seeds, mean±std, CV<15% gate on the scratch control. Load when running any benchmark that reports a metric."
---

# HRNet — Multi-Seed Reproducibility

A single-seed number is not admissible as a result. Every reported metric is a
distribution over seeds, and the measurement harness itself must be stable before
any capability claim is trusted.

## The rules

- **≥5 seeds** for every reported metric. Report `mean ± std` (and the seed list).
- **Same seed → identical curve.** Step and data curves must be bitwise
  reproducible (see `hrnet-torch-experiment-engineering` for seeding). A harness
  test asserts this.
- **CV gate on the control.** The coefficient of variation
  `CV = std / mean` of the threshold metrics (`steps_to_threshold`,
  `data_to_threshold`) on the **scratch** control must be `< 0.15`. If it is not,
  the metric is too noisy to detect a real effect and the harness is rejected —
  fix the metric/threshold definition before reporting any comparison.
- **Walltime is reported, never gated.** `walltime_to_threshold` is
  nondeterministic; include it for context but never use it in a reproducibility
  or pass/fail gate.

## Seed-loop structure

```python
def multiseed(run_one, seeds=(0, 1, 2, 3, 4)) -> dict:
    rows = [run_one(seed) for seed in seeds]            # each: dict of metrics
    keys = rows[0].keys()
    agg = {}
    for k in keys:
        vals = torch.tensor([float(r[k]) for r in rows])
        mean = vals.mean().item()
        std = vals.std(unbiased=True).item()
        agg[k] = {"mean": mean, "std": std,
                  "cv": (std / mean) if mean else float("inf"),
                  "values": [r[k] for r in rows]}
    return agg
```

## Comparing against a control

A delta is real only when it survives seed noise. Compare distributions, not
point estimates:

- Report `mean(metric, M)` vs `mean(metric, control)` with both stds.
- A claimed improvement should clear the noise band (e.g. the means differ by
  more than the combined std, or a simple paired comparison across the shared
  seed set). State which test you used.
- Never report "loss went down" — report "`steps_to_threshold` was X ± a for M
  vs Y ± b for scratch over seeds {0..4}" (see `hrnet-scientific-method-controls`).

## What to do when CV is too high

Likely causes, in order: threshold defined on a noisy single-batch metric (smooth
it / use a moving average); learning rate too high (crossing is jittery); too few
seeds (raise to 7–10); capability metric is discrete with few samples (increase
eval set). Fix the cause, do not just widen the gate.
