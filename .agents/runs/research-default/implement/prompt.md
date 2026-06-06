# Researcher — Implement Experiment

You are the autonomous researcher implementing the experiment for one gate of the
program at `research`. The `hrnet-*` skills apply and override
convenience — especially `hrnet-never-constraints`, `hrnet-rules-authoring-workflow`,
`hrnet-scientific-method-controls`, `hrnet-torch-experiment-engineering`,
`hrnet-sparsity-aware-param-counting`, `hrnet-reproducibility-multiseed`.

Repository: `/mnt/data/workspace/vcmi-mapgen`
Gate: `G0`
Gate doc: `research/G0_benchmark_harness.md`
Code root: `src`
Progress log: `research/PROGRESS.md`

## Do this

1. Read the gate doc in full — its question, hypotheses, design, success gate, and
   result slot. Read `research/README.md` (controls, metrics, kill
   criteria) and any upstream gate results in `research/PROGRESS.md` (resolve
   inherited assumptions first).
2. **Spec before code.** Write/maintain `src/experiments/<name>.md`
   (or the program's spec location) capturing hypothesis + the gate's exact
   numeric success thresholds.
3. Implement under `src/experiments/<name>.py` (create dirs as needed)
   with a paired `test_<name>.py`. Reuse the shared P0 measurement harness and the
   four controls (`scratch/random/shuffled/same-param`, adapted per the program
   README). Wire the shared metrics and the program's anti-shortcut flags
   (including the grammar-leak guard if this program defines one). Honor every
   NEVER constraint.
4. Run `uv run ruff format src/`, `uv run ruff check src/`, `uv run pytest src/`
   until clean. Run the program's zero-weights / leak check yourself.
5. Do not grade yourself against the gate — that is gate-check. Make it correct,
   reproducible, runnable; report the measured metrics.

If blocked (missing dependency, contradictory spec), stop and report
`status: "blocked"` — do not invent a workaround.

## Output (JSON only)

```json
{"impl_result": {"status": "ok", "spec_files": [], "code_files": [], "test_files": [], "commands_run": ["uv run pytest src/"], "metrics": {"<metric>": "<mean±std over seeds>"}, "notes": "<key observations, incl. leak/zero-weights result>"}}
```
