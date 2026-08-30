---
name: vcmi-mapgen-python-cli
description: "Generic Python CLI conventions — Python 3.12+, type hints, absolute imports, uv, linting with ruff AND ty (zero findings, `# ty: ignore` not `# type: ignore`), entry points, exit codes, logging, subprocess, pathlib, JSON I/O, error handling, naming. Package structure, interfaces, typed values, and dependency injection live in python-architecture. Applies to all Python files."
metadata:
  generated_by: farrier
  source: library/skills/stacks/python/python-cli/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-python-cli/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [python, cli, standards, entrypoint]
---

# Python CLI — Core Conventions

**Applies to every Python file in this repo.**

---

## Language & headers

- Python **3.12+**. Every module starts with `from __future__ import annotations`.
- Full type hints on all public functions and class fields.
- No `*` imports — explicit imports only.

## Imports — absolute from the package root, always

Every import names its package from the root. Relative imports are not accepted,
including the single-dot sibling form.

```python
# Yes — the reader sees which package the name lives in, and grep finds it.
from workhorse.requirements import Requirement
from workhorse.graph import nodes

# No — both are relative.
from ..requirements import Requirement
from .model import Graph
```

A relative import only resolves when the module is imported as part of its package,
so it breaks the moment the file is run directly. It also hides provenance: `.model`
tells the reader nothing about where the code lives, and a rename can't be found by
searching for the package name.

This is enforced, not just advised — ruff's `TID252` with
`ban-relative-imports = "all"` fails the lint. `ruff check --select TID252 --fix
--unsafe-fixes` rewrites existing relative imports to absolute (the fix is "unsafe"
only in that it edits imports; verify by running the suite afterwards).

## `sys.path` manipulation is prohibited

Never write this, in source or in tests:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # prohibited
sys.path.append(...)                                            # prohibited
```

An unresolved import is a **packaging** problem and gets a packaging fix — add the
package as a workspace member, or install it into the venv — never path surgery at
import time. `sys.path` edits are order-dependent global state: they work in the one
file that does them and leave every other entry point broken.

In a test file this is always redundant: `uv sync` installs the package, so
`from mypkg.thing import X` already resolves — under pytest **and** under a direct
`python tests/test_x.py`. An insert there only shadows the installed package with a
directory that happens to look like it.

The one narrow exception is a harness that **emulates the interpreter** rather than
patching around a packaging gap: running a standalone script in-process has to
reproduce what `python script.py` does — CPython puts the script's own directory on
`sys.path[0]` — or a sibling import resolves in production and fails only in the test.
Scope it to the call and restore `sys.path` afterwards:

```python
@contextmanager
def script_dir_on_path(directory: Path):
    saved = sys.path[:]
    sys.path.insert(0, str(directory))
    try:
        yield
    finally:
        sys.path[:] = saved
```

If you reach for that anywhere other than "I am standing in for the interpreter",
it's the prohibited kind.

## uv is the package manager

Dependencies and the package itself resolve through **uv**, not ad hoc paths.

```bash
uv sync                    # install the workspace + dev group into .venv
uv add httpx               # add a dependency (edits pyproject.toml + uv.lock)
uv run pytest              # run inside the synced environment
```

- Declare dependencies in `pyproject.toml`; let `uv.lock` pin them. Never
  `pip install` into a uv-managed venv — the next `uv sync` reverts it.
- Local packages that import each other are **workspace members**
  (`[tool.uv.workspace] members = [...]`), which makes them importable by their real
  package name everywhere.
- Tests import the package exactly like any consumer does — `from workhorse.requirements
  import Requirement`. `uv sync` installs the workspace, so a test file needs no
  path setup at all.

### The one exception: scripts that run outside a uv project

A script the workflow runner executes standalone (e.g. a workhorse workflow script)
has no package to import from. It gets **no** `sys.path` insert — it must be a single
**monolithic file**: stdlib imports only, no local imports, everything it needs inlined.

If such a script has grown past what one file can hold, that is the signal to make it a
real package with a console entry point, not to reach for `sys.path`.

## Linting is two tools — `ruff` **and** `ty`

A change is not done until both are clean, from the repo root so every member package is
covered in one pass:

```bash
uv run ruff check .          # style, imports, dead code
uv run ty check              # types
```

Wire both into the same target, and make the test target depend on it, so "clean" is one
thing and not a habit:

```makefile
lint:
	uv run ruff check .
	uv run ty check

test: lint
	uv run pytest
```

They do not overlap: ruff reads one file at a time and never resolves a name to its
definition, so the failures below are invisible to it and to review, and visible to `ty`
the moment it is turned on.

- A call that passes an argument the callee does not declare, or omits one it requires.
- An attribute read off a `T | None` the caller never narrowed — the null-object rule in
  `python-architecture` exists precisely to keep these from being written.
- A test double that has drifted from the port it stands in for: a method the port
  renamed, a parameter it added, a return type it changed. This is the largest single
  category in practice, and the reason the doubles-are-the-port rule in
  `python-testing` is enforceable rather than advisory.

**Zero findings is the bar, for both, and for test files too.** Every ty rule stays at its
default severity — an ignore list is a rule nobody can tell is being violated. Where a
whole file is genuinely not checkable (generated code, a doc fixture that is not importable),
exclude the *path*, in `[tool.ty.src] exclude = [...]`, so the exception is one visible
line rather than a rule silently weakened everywhere.

### Fix the finding, don't suppress it

Almost every diagnostic names a real defect in the code or in a double. The fixes that
recur, in the order to reach for them:

| Diagnostic | The fix that is not a suppression |
|---|---|
| `X` has no attribute `y` on a `T \| None` | Narrow at the point the caller knows: `assert v is not None`, or a shared `present(v)` helper that carries the reason in its docstring |
| A double is "not assignable to" the port | Make it **be** the port — subclass the ABC/Protocol/dataclass and override only what the test scripts |
| Assigning over a method or a module-level function | `unittest.mock.patch.object` (or `monkeypatch.setattr`), or a subclass with a declared field. An instance attribute shadowing a method is invisible to the reader and outlives the test that set it |
| A heterogeneous dict literal | Declare it — a `TypedDict` (or a frozen dataclass) instead of letting every value become the union of all of them |
| `Any` sneaking through a `ParamSpec` alias | The gradual form is `Callable[..., R]` / `X[...]`, never `X[Any]` |

When a diagnostic really is the point of the code — a wrong-typed argument under
`pytest.raises`, a deliberately malformed call the runtime guard exists to reject — suppress
that one line and say why:

```python
with pytest.raises(TypeError):
    # The wrong type is the subject of the test, so the checker is told to allow
    # the one call the runtime guard exists to reject.
    console_script("demo")  # ty: ignore[invalid-argument-type]
```

`# type: ignore[...]` is **mypy's** spelling and is inert for ty — a file full of them is
unchecked, not clean. The ty spelling names the rule: `# ty: ignore[unresolved-attribute]`.
When migrating a repo, treat every existing `# type: ignore` as an unreviewed finding.

## Entry point pattern

```python
from __future__ import annotations

def main() -> None:
    ...

if __name__ == "__main__":
    main()
```

## Exit codes

Use `raise SystemExit(code)` — never `sys.exit()` in library code.

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error |
| 2 | Usage / argument error |

## Logging — never bare `print()` for diagnostics

```python
import logging

logger = logging.getLogger(__name__)   # name = module path, set automatically

# Entry point only — configure once:
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(name)s %(levelname)s: %(message)s",
)

logger.info("Processing %s items", count)
logger.warning("Skipping %s: %s", path, reason)
logger.error("Failed: %s", err)
```

`logging.getLogger(__name__)` is stateless — no global mutable prefix, no `set_script_name()`.

## Subprocess — prefer Python libraries; subprocess only for CLI tools

```python
import subprocess

# Only when no Python library exists for the command:
result = subprocess.run(
    ["gh", "pr", "create", "--title", title],
    capture_output=True,
    text=True,
    check=False,
    cwd=str(repo_path),
)
if result.returncode != 0:
    raise RuntimeError(f"gh pr create failed: {result.stderr.strip()}")
```

**Prefer purpose-built Python libraries over subprocess:**
- Git operations → `from git import Repo` (gitpython), not `subprocess.run(["git", ...])`
- File parsing → stdlib (`json`, `yaml`, `configparser`), not shell pipelines
- HTTP → `httpx` or `urllib`, not `curl`

Never use `shell=True`.

## Filesystem — `pathlib.Path` over string paths

```python
from pathlib import Path

root = Path(__file__).resolve().parent
config = root / "config.json"
data = config.read_text(encoding="utf-8")
```

Prefer `Path` values throughout the codebase. Convert to `str(path)` only at API boundaries that require strings, such as subprocess `cwd` or third-party libraries without `PathLike` support.

## JSON I/O

```python
import json
from pathlib import Path

def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

Always specify `encoding="utf-8"` — never rely on platform default.

## Structured data and code organization → `python-architecture`

Which typed value to reach for (Pydantic vs frozen dataclass vs `TypedDict`), when a group of
functions becomes a class, how services are wired, and where interfaces and their implementations
live are all in
[`../vcmi-mapgen-python-architecture/SKILL.md`](../vcmi-mapgen-python-architecture/SKILL.md).
They are not repeated here, because they were losing to the CLI mechanics around them.

The three that decide most day-to-day calls:

- **Anything parsed, validated, or coerced from outside the current function uses Pydantic** — CLI
  config, JSON/YAML/TOML files, environment-derived settings, API payloads, script input. *And
  anything we serialize and read back later counts as outside data*, whoever wrote it.
- **Trusted in-memory records are `@dataclass(frozen=True, slots=True)`.** Never
  `-> dict[str, Any]` with the keys listed in the docstring.
- **Use `Path` composition for filesystem logic**; never assemble paths from plain strings.

## Error handling

```python
try:
    result = do_work(path)
except FileNotFoundError:
    logger.error("File not found: %s", path)
    raise SystemExit(1)
except ValueError as exc:
    logger.error("Invalid input: %s", exc)
    raise SystemExit(2)
```

Wrap exceptions with context. Fail fast. Never swallow exceptions silently.

## Naming

| Category | Convention |
|----------|-----------|
| Modules, functions, variables | `snake_case` |
| Classes | `PascalCase` |
| Constants | `UPPER_CASE` |
| Private module helpers | `_leading_underscore` (not exported) |
| Public API | no underscore prefix |

Private helpers (underscore prefix) are an implementation detail of their module — do not export them from a shared library.
