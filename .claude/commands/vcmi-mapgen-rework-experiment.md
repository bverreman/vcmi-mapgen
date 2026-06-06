# HRNet Researcher — Rework Experiment

The gate check returned `needs_rework`. Apply the autonomous-iteration protocol
from `hrnet-never-constraints`. The other `hrnet-*` skills still apply.

Repository: the current repository (your working directory).
Gate: the gate under rework.

## Failures to address

Use the `failed_criteria` and notes from the gate check (the structured list of
criterion / expected / observed entries that did not pass).

## Do this

1. State the failure clearly. List ALL plausible root causes — do not skip this.
2. Fix the **first** cause only. Do not invent a new approach or change the
   hypothesis. Do not introduce any NEVER-listed shortcut to force a pass.
3. Re-run `uv run ruff check src/` and `uv run pytest src/` until clean; re-run
   the measurement and the zero-weights test.
4. Update the experiment spec/code in place (same files). Keep the provenance
   header and the controls intact.

Report the same `impl_result` shape so the gate check can re-run. Note which
cause you addressed and which remain.

## Output (JSON only)

```json
{"impl_result": {"status": "ok", "spec_files": [], "code_files": [], "test_files": [], "commands_run": ["uv run pytest src/"], "metrics": {"<metric>": "<mean±std>"}, "notes": "<cause addressed; causes remaining>"}}
```
