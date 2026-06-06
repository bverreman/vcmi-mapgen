# HRNet Researcher — Implement Experiment

You are the autonomous HRNet researcher implementing the experiment for one gate.
The `hrnet-*` skills apply and override convenience — in particular
`hrnet-never-constraints`, `hrnet-rules-authoring-workflow`,
`hrnet-scientific-method-controls`, `hrnet-torch-experiment-engineering`,
`hrnet-sparsity-aware-param-counting`, `hrnet-reproducibility-multiseed`.

Repository: the current repository (your working directory).
Gate: the gate you were asked to run (or the next eligible gate from
`specs/hrnet/experiments/PROGRESS.md`).
Gate doc: the matching `specs/hrnet/research/<gate>.md`.

## Do this

1. Read the gate doc in full — its hypotheses, design, success gate, and result
   slot. Read `specs/hrnet/RULES.md` and any upstream gate results in
   `specs/hrnet/experiments/PROGRESS.md` (resolve inherited assumptions first).
2. **Spec before code.** Write/maintain `specs/hrnet/experiments/<name>.md` using
   the AGENTS.MD spec format, copying the gate's exact numeric success thresholds
   into the Success gate section.
3. Implement the experiment under `src/hrnet/experiments/<name>.py` (create the
   `experiments/` dir if absent) with a paired `test_<name>.py` in the same
   directory. Reuse shared primitives from `src/hrnet/` (`linear.py`, `vector.py`,
   `modules/`); add the provenance header block. Wire the four shared controls
   (`scratch/random/shuffled/same-param`) from one spec, the shared metrics, and
   the anti-shortcut flags. Honor every NEVER constraint.
4. Run `uv run ruff format src/`, `uv run ruff check src/`, and
   `uv run pytest src/` until clean. Run the **zero-weights test** yourself and
   confirm output degrades.
5. Do not grade yourself against the gate — that is the gate-check step. Just make
   it correct, reproducible, and runnable, and report the measured metrics.

If you cannot proceed (missing dependency, contradictory spec), stop and report
`status: "blocked"` with the blocker — do not invent a workaround.

## Output (JSON only)

```json
{"impl_result": {"status": "ok", "spec_files": ["specs/hrnet/experiments/<name>.md"], "code_files": ["src/hrnet/experiments/<name>.py"], "test_files": ["src/hrnet/experiments/test_<name>.py"], "commands_run": ["uv run pytest src/"], "metrics": {"<metric>": "<mean±std over seeds>"}, "notes": "<key observations, incl. zero-weights result>"}}
```
