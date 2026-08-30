---
name: vcmi-mapgen-code-structure
description: "The language-neutral rules for where code lives *inside* a layer — when a pile of functions becomes an object, when a module becomes two, when a value crossing a boundary needs a name, and where configuration and side effects are allowed to appear. Every rule carries a mechanically detectable trigger, so a violation is a finding rather than a matter of taste. Load when adding a module, growing a parameter list, choosing between a function and a class, or reviewing structure; hexagonal-architecture governs the boundaries *between* layers, and the stack architecture skill (go-architecture, python-architecture, flutter-architecture, typescript-architecture) supplies the mechanics. Applies to **/*.go,**/*.dart,**/*.ts,**/*.tsx,**/*.py."
metadata:
  generated_by: farrier
  source: library/skills/architecture/code-structure/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-code-structure/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [standards]
---

# Code Structure — Rules That Can Fire

[`../vcmi-mapgen-hexagonal-architecture/SKILL.md`](../vcmi-mapgen-hexagonal-architecture/SKILL.md)
governs the boundaries **between** rings: which way dependencies point, what a port may name, where
infrastructure is allowed to live. This skill governs the inside of a single ring — the decisions
that never trip a layering check and produce most of the damage anyway: a 1,600-line module, a
twelve-parameter function, a dict returned where a type belonged, an environment variable read at
import time.

## Every rule owes a trigger

Most structural guidance is unfalsifiable. "Prefer small modules." "Model state and behavior as a
class." Both are true; neither has ever stopped anyone, because you get to decide *after the fact*
whether the module was small enough or the behavior meaningful. Compare a rule like "no relative
imports": it fires, so it holds.

So every rule below has four parts, and a candidate rule that cannot fill all four does not belong
in this file:

| Part | What it must be |
|---|---|
| **Statement** | what to do |
| **Trigger** | a *shape in the code* a reader or a grep can detect — never a judgment |
| **Fix** | the specific transformation |
| **Counter-case** | when the trigger fires and you are still right |

The counter-case is not politeness. A rule with no stated exception gets applied where it does
harm, and then gets abandoned entirely.

---

## How to use this file

**Scan the trigger table at the bottom.** It is the whole rule set, one row per rule, each row a
shape you can detect by reading or grepping. Nothing else here is needed to *notice* a violation.

When a row fires, open the file holding it for the statement, the fix, the counter-case, and the
reason the rule exists. Each carries a quarter of the rule set:

- **[references/objects.md](references/objects.md)** — rules 1.1–1.4. When a pile of functions
  becomes an object, and the stop condition that says it does not. Read it when you are weighing a
  function against a class, growing a parameter list, or looking at module-level mutable state.
- **[references/modules.md](references/modules.md)** — rules 2.1–2.2. When one module is really
  two. Read it when writing a module's docstring, or when an entry point started doing the work.
- **[references/boundaries.md](references/boundaries.md)** — rules 3.1–3.3. Values written and read
  back, returns with several pieces, and payloads from a schema you do not own. Read it when
  designing a checkpoint, a wire format, or a reader for another tool's output.
- **[references/config-and-effects.md](references/config-and-effects.md)** — rules 4.1–4.3. Where
  configuration may be read and where a side effect may live. Read it when adding a setting, a
  default parameter, a cache write, or a `sleep`.

Rule 5 stays here, because it is the one to carry without looking anything up.

---

## 5. The question that catches most of the above

**A monkeypatched private name is a missing injection point.**

**Trigger.** A test that reaches into a module and replaces a private/internal function, or that
reassigns module state to set up a scenario.

**Fix.** Whatever the test needed to control is a dependency. Inject it.

Keep this one close, because it is the cheapest available proxy for every rule here. Ports that
name only domain types, absent collaborators that are null objects, injected settings, classifiers
free of I/O, an injected clock — all of them pay off in one observable currency: **can this be
tested without patching?** A reviewer who cannot recall the taxonomy can still ask that.

The tell that a patch-based seam has gone wrong is when a test must know the *wrong component's*
internals to set up its scenario — faking any backend's failure by patching one specific backend's
private function, say. At that point the seam is not merely informal, it is in the wrong place.

**Counter-case.** Patching at a genuine third-party boundary you do not own and cannot inject
around — the standard library's clock, the process table. Prefer a thin owned wrapper even there,
but this is the exception that is real.

---

## Summary of triggers

| # | Trigger — the shape you can detect | Rule |
|---|---|---|
| [1.1](references/objects.md) | 3+ functions sharing the same leading parameters | fields, not parameters |
| [1.2](references/objects.md) | 2+ functions touching the same module-level mutable | that state is an object |
| [1.3](references/objects.md) | a container created only so a closure can write to it | the closure set is an object |
| [1.4](references/objects.md) | a class with no fields | make it a module |
| [2.1](references/modules.md) | a module docstring that needs bullets | one module per bullet |
| [2.2](references/modules.md) | wiring and >1 command body in one file | one module per command |
| [3.1](references/boundaries.md) | literal in, key-lookup-with-default out | one model owns both directions |
| [3.2](references/boundaries.md) | 3+-tuple, documented map keys, mutated argument | a named record |
| [3.3](references/boundaries.md) | a strict model mirroring a foreign schema | tolerant read, owned type |
| [4.1](references/config-and-effects.md) | config read at module scope or below the edge | inject immutable settings |
| [4.2](references/config-and-effects.md) | a decision function that writes or sleeps | split; inject the clock |
| [4.3](references/config-and-effects.md) | an effect in a function named for something else | move it to the invariant's owner |
| [5](#5-the-question-that-catches-most-of-the-above) | a test patching a private name | add the injection point |

When you add a rule to this file, add its row. A rule with no row is a preference, and preferences
belong in a review comment rather than a skill.
