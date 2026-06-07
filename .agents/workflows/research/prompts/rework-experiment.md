# Researcher — Rework Experiment

The gate check returned `needs_rework`. Apply the autonomous-iteration protocol
from `hrnet-never-constraints`. The other `hrnet-*` skills still apply.

Repository: `{{ repo_dir | default('.') }}`
Gate: `{{ gate_id }}`
Code root: `{{ code_root }}`
Rework round: `{{ rework_count | default('?') }}`

## Time budget — ~{{ node_timeout_min | default(10) }} min wall-clock

This turn has ~{{ node_timeout_min | default(10) }} minutes ({{ node_timeout_s | default(600) }}s).
A command that runs past it is **killed** and this node restarts from scratch with
**no memory** — the budget is wasted and the same fix never lands. So time any
measurement before running it at full scale, reuse cached rebuilds when their inputs
are unchanged, and if the full run won't fit, iterate at a reduced scale and report
what you ran (the gate check re-runs the full seed set).

## Failures to address

```json
{{ failed_criteria | default('[see the gate-check notes]') }}
```

Reviewer notes: {{ notes | default('(none)') }}

## Do this

1. State the failure clearly. List ALL plausible root causes — do not skip this.
2. Fix the **first** cause only. Do not invent a new approach or change the
   hypothesis. Introduce no NEVER-listed shortcut to force a pass.
3. Re-run `uv run ruff check src/` and `uv run pytest src/` until clean; re-run
   the measurement and the leak/zero-weights check.
4. Update the experiment spec/code in place under `{{ code_root }}`. Keep the
   provenance header and the controls intact.

Report the same `impl_result` shape so the gate check can re-run. Note which cause
you addressed and which remain.

## Output (JSON only)

```json
{"impl_result": {"status": "ok", "spec_files": [], "code_files": [], "test_files": [], "commands_run": ["uv run pytest src/"], "metrics": {"<metric>": "<mean±std>"}, "notes": "<cause addressed; causes remaining>"}}
```
