---
name: vcmi-mapgen-python-architecture
description: "Python mechanics for the ports-and-adapters contract and the code-structure rules — Protocol vs ABC, the port package layout (interface at the root, adapters beneath, registry apart), which typed value to reach for (Pydantic / frozen dataclass / TypedDict / NamedTuple) and why round-trip beats provenance, services built by dependency injection from a frozen settings object, null objects instead of Optional collaborators, injected clocks, and the ruff / ty / import-linter rules that turn each of these into a gate. Load when structuring any Python package beyond a single module. Applies to **/*.py."
metadata:
  generated_by: farrier
  source: library/skills/stacks/python/python-architecture/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-python-architecture/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [python, backend, standards]
---

# Python — Architecture Mechanics

This skill is the **Python spelling** of two language-neutral contracts. Read them first; the *why*
lives there and is not repeated here:

- [`../vcmi-mapgen-hexagonal-architecture/SKILL.md`](../vcmi-mapgen-hexagonal-architecture/SKILL.md)
  — dependency direction, domain purity, port purity, generic-engine-vs-port, testability.
- [`../vcmi-mapgen-code-structure/SKILL.md`](../vcmi-mapgen-code-structure/SKILL.md)
  — when functions become an object, when a module becomes two, data at boundaries, configuration
  and effects.

For CLI conventions (exit codes, logging, `pathlib`, subprocess, imports) see
[`../vcmi-mapgen-python-cli/SKILL.md`](../vcmi-mapgen-python-cli/SKILL.md).

---

## Port package layout

The contract's file layout, in Python:

```text
acme/backends/
    __init__.py     # the Protocol/ABC and nothing else — imports no sibling here
    claude.py       # one adapter per module
    codex.py
    copilot.py
    registry.py     # name → class; the ONLY module importing every adapter
```

`__init__.py` holding the port is deliberate: `from acme.backends import AgentBackend` is the
import an adapter needs, and it must not drag in the other adapters. Put the registry in
`__init__.py` and it does exactly that.

**Never** import an adapter module from the package that declares the port.
**Never** put the registry beside the port.

## `Protocol` vs `ABC`

Default to `typing.Protocol`. Reach for `ABC` only when the port supplies shared behavior.

```python
from typing import Protocol

class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...
```

`Protocol` is structural, so an adapter satisfies it **without importing it** — which means the
dependency arrow points inward for free, and a test fake is any object with the right methods. An
`ABC` forces `class X(Port)`, i.e. an adapter→port import edge, which is harmless when the port is
in your own core and wrong when you are typing something you do not own.

Use `ABC` when there is real shared implementation — a template method, a common `harness_env()`,
default behavior every adapter inherits. In that case the inheritance is the point, not an
accident. Do not use `@runtime_checkable` as a substitute for real typing: it checks method
*names* only, so it will happily accept an object whose signatures are wrong.

## Which typed value

| The value is… | Reach for |
|---|---|
| parsed, validated, or coerced from outside the process — CLI args, config files, API payloads, another tool's output | `pydantic.BaseModel` |
| written by us and read back later — checkpoints, state files, caches, queue messages | `pydantic.BaseModel` (see below) |
| an in-memory record that never leaves the process | `@dataclass(frozen=True, slots=True)` |
| a foreign payload we read tolerantly and do not own | `dict[str, Any]` at the read; `TypedDict` only if the subset you rely on is stable enough to name |
| a small multi-value return | `@dataclass(frozen=True, slots=True)`; `NamedTuple` only when positional unpacking is genuinely wanted |
| settings assembled at the entry point | frozen dataclass + `from_env` classmethod, or `pydantic-settings` |

**Round trip beats provenance.** The second row is the one that gets argued about, so state the
rule plainly: *it does not matter that our own code wrote it.* A checkpoint written in-memory and
read back at hour thirty of a long run has, in between, been exposed to a version change, a partial
write, and — for anything an operator is meant to fix by hand — an editor. Provenance is the wrong
axis; surviving the process is the right one.

The shape that follows:

```python
class Checkpoint(BaseModel):
    engine: Literal["pyflow"]        # a fail-closed discriminator, not a comment
    run_id: str
    state: str
    params: dict[str, Any]           # opaque by design — see the tolerant-read row
    updated_at: datetime

# writing and reading are the same type, in both directions
path.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
checkpoint = Checkpoint.model_validate_json(path.read_text(encoding="utf-8"))
```

**Never** build the payload as a dict literal at the write and pull it apart with `.get()` at the
read. That is the same type declared twice, in two places, neither of them checked.

Frozen dataclasses use `slots=True` unless something needs `__dict__`: it costs nothing, blocks
attribute typos, and makes the "this is a record" intent unambiguous.

## No `dict[str, Any]` return type

`-> dict[str, Any]` on a function whose docstring lists the keys is a missing type, and the
docstring is the evidence — someone knew the shape well enough to write it down, in the one place
`mypy` cannot read.

```python
# ✗ nine documented keys, none of them checked, every caller a string literal away from a bug
def counts(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns total, done, active, blocked, remaining, pct, ..."""

# ✓
@dataclass(frozen=True, slots=True)
class Counts:
    total: int
    done: int
    active: int
    blocked: int
    remaining: int
    pct: float

def counts(items: Sequence[WorkItem]) -> Counts: ...
```

The same applies to a tuple return with more than two elements, or with two elements of the same
type. `-> tuple[str, str | None, str, bool, bool, float | None, int]` is a record whose fields were
never named; every caller decodes seven positions by counting, and adding a field edits every call
site.

**Never** mutate a dict argument and have the caller read it back afterwards — return a new value.
An in-place accumulator is why call sites end up writing `state.setdefault("acc", {})`.

## Services and dependency injection

A class earns its existence by holding **state with invariants** or by being a **seam**. A class
with no fields is a module that took a wrong turn — keep those functions in a well-named module
instead.

When it does earn it, build it by injection, from a frozen settings object assembled at the entry
point:

```python
@dataclass(frozen=True)
class Resilience:
    max_retries: int = 3
    backoff_base_s: float = 1.0
    result_timeout_s: float = 900.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Resilience:
        return cls(
            max_retries=_int(environ, "ACME_MAX_RETRIES", 3),
            backoff_base_s=_float(environ, "ACME_BACKOFF_BASE_S", 1.0),
            result_timeout_s=_float(environ, "ACME_RESULT_TIMEOUT_S", 900.0),
        )

@dataclass(frozen=True)
class TurnRunner:
    backend: AgentBackend
    resilience: Resilience
    clock: Clock
    workdir: Path
```

- **One read, at the edge.** `os.environ` is read inside `from_env` and nowhere else. A module-scope
  `TIMEOUT = int(os.environ.get(...))` freezes before any test or caller can influence it, and the
  only way to exercise the other branch is to reload the module.
- **Overrides copy, never mutate:** `dataclasses.replace(resilience, max_retries=1)`.
- **A default parameter must not come from configuration.** `def run(..., timeout=DEFAULT_TIMEOUT)`
  binds at `def` time — configuration that looks live and is not. Default to `None` and resolve
  from the injected settings inside the body.
- Settings derived from the environment are, strictly, parsed outside data and belong in Pydantic
  (`pydantic-settings`). A frozen dataclass with an explicit, total `from_env` is an acceptable
  substitute in a package that must stay dependency-light — provided every coercion lives in that
  one classmethod.

## Optional collaborators are null objects

```python
class Telemetry(Protocol):
    def record(self, event: Event) -> None: ...

class NullTelemetry:
    def record(self, event: Event) -> None:
        return None

_active: Telemetry = NullTelemetry()     # never None
```

**Never** a module-level `_active: X | None` guarded at every call site, and **never** a facade
that dispatches by string name:

```python
def _call(method: str, *args: Any) -> None:     # ✗
    if _active is None:
        return
    try:
        getattr(_active, method)(*args)
    except Exception:
        pass
```

That defeats rename, signature checking, and find-usages at once, and with the bare `except` a
typo in `method` becomes a permanent silent no-op. Fail-soft is a legitimate policy for
telemetry — implement it as one decorator on the real class, over a typed Protocol.

## The clock and the process are dependencies

`time.sleep`, `datetime.now`, and `subprocess` are I/O. A retry ladder that sleeps for real is a
test suite that either takes minutes or patches private names to avoid it; neither exercises the
ladder. Inject a `Clock` and "wait eight hours for the rate-limit window to reopen" costs
microseconds.

Module-level process state — a `_active_proc` plus its lock, mutated by several functions — is an
unnamed object. Make it a class; the caller then decides how many exist, and the signal path
becomes testable.

For a flag shared with a callback, use the real primitive:

```python
fired = {"v": False}          # ✗ a bool wearing a costume
fired = threading.Event()     # ✓
```

## Deferred imports

An import inside a function body is an **optional dependency** or a **cycle**. In Python the
optional case is recognisable by its handler:

```python
def run_tests() -> int:
    try:
        import pytest                       # ✓ genuinely absent in a normal install
    except ImportError:
        logger.error("pytest is not installed; install the 'dev' extra")
        raise SystemExit(1)
```

No handler means it was a cycle, and the fix is the dependency graph — in practice, a concrete
adapter's code sitting inside the generic ring. Move it behind the port and the deferred import
disappears with it.

The one clean exception is annotation-only:

```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acme.backends.claude import ClaudeBackend    # ✓ never executed at runtime
```

## Making it a gate

| Obligation | Python check |
|---|---|
| Absolute imports only | ruff `TID252` with `ban-relative-imports = "all"` |
| No `Any` in a port signature | ruff `ANN401` (`flake8-annotations`), scoped to the package holding the ports |
| Dependencies point inward | `import-linter` contracts in `pyproject.toml` — a `layers` contract for the rings, `forbidden` for "core must not import adapters" |
| The port package imports no adapter | `import-linter` `forbidden` contract from the port module to each adapter module |
| No generic module naming one adapter | `grep -n 'claude\|codex\|copilot' <generic module>` — zero hits is the bar |
| A function grew into an object | ruff `PLR0913` (too many arguments); a twelve-parameter function is nine pieces of context that lost their home |
| Missing seams | `grep -rn 'monkeypatch.setattr' tests/ \| grep '_'` — each patch of a private name is a dependency that should have been injected |
| Every adapter implements the whole port | `ty check` — a partial implementation, a renamed method, a parameter one adapter added, are all `invalid-method-override` or an abstract-instantiation error |
| A collaborator is a null object, not `Optional` | `ty check` — every unguarded read off a `T \| None` is `unresolved-attribute`, so an `Optional` collaborator cannot stay one silently |
| Injected, not assigned over | `ty check` — an assignment over a method or a module-level function is `invalid-assignment`, which is what makes injecting the seam cheaper than patching one |
| A double stands in for the real port | `ty check` — a structural look-alike stops being assignable the moment the port changes, which is exactly when a stale fake is worth hearing about |

The last four are why **`ty` runs alongside ruff** — setup and the zero-findings bar are in
[`../vcmi-mapgen-python-cli/SKILL.md`](../vcmi-mapgen-python-cli/SKILL.md). Ruff reads one
file at a time and never resolves a name to its definition, so no ruff rule can see an adapter
that has drifted from its port. That is the failure this architecture is *most* exposed to: the
whole point of a port is that the caller is written against the interface and never sees the
adapter, so nothing else notices when the two stop agreeing.

Wire the checks when you add the ports. A contract that is only prose is a contract that erodes the
first time a deadline is close.

## Scope boundaries

This file covers package layout, interfaces, typed values, injection, and the checks. It does
**not** cover CLI conventions — see
[`../vcmi-mapgen-python-cli/SKILL.md`](../vcmi-mapgen-python-cli/SKILL.md). It does **not**
cover test design or pytest mechanics — see
[`../vcmi-mapgen-python-testing/SKILL.md`](../vcmi-mapgen-python-testing/SKILL.md).
