---
name: vcmi-mapgen-repo-python-conventions
description: "Python/tooling conventions for the assembler repo — Python 3.12, future annotations, ruff, uv, test pairing and naming. Load when writing or editing any Python under src/."
---

# HRNet — Repo Python Conventions

## Language & headers

- Python **3.12**. Every module starts with `from __future__ import annotations`.
- Full type hints on public functions and dataclass fields. Match the existing
  style in `src/hrnet/` (frozen dataclasses, explicit tensor shapes in
  docstrings, `nn.Module` subclasses with keyword-only `__init__` args).

## Tooling (exact commands)

Run from the repo root, inside the project venv (`uv` manages it):

```bash
uv run ruff format src/      # format — run before committing
uv run ruff check src/       # lint — must be clean
uv run pytest src/           # tests — discovers test_*.py under src/
```

- Mark long training runs `@pytest.mark.slow` so the default suite stays fast
  (`-m "not slow"` excludes them; `-m slow` runs them). pytest `testpaths=["src"]`.
- Add deps only with `uv add <pkg>` — never hand-edit lockfiles.

## Test pairing & naming

- Every experiment module `foo.py` has a paired test in the **same directory**.
- **Match the naming convention already used by the sibling files in the
  directory you are editing.** In `src/hrnet/` and `src/hrnet/experiments/` the
  existing tests are named `test_<module>.py` (e.g. `test_hrnet_loop.py`) — use
  that form there. (RULES.md text shows an older `<module>_test.py` convention
  from a different tree; follow the live siblings, not the stale doc.)
- A minimum test constructs the component, runs one forward on synthetic data,
  asserts output shape, and asserts `torch.isfinite(out).all()`. See
  `hrnet-torch-experiment-engineering`. Correctness/threshold tests are
  encouraged on top of that minimum.

## Where experiment code lives

- Experiment code: `src/hrnet/experiments/<name>.py` (+ `test_<name>.py`).
- Create `src/hrnet/experiments/` if it does not exist yet (it is discovered by
  `testpaths=["src"]` automatically — no config change needed).
- Reuse shared primitives from `src/hrnet/` (`linear.py`, `vector.py`,
  `modules/`). Do not copy them into the experiments dir.
