---
name: vcmi-mapgen-hexagonal-architecture
description: "The universal ports-and-adapters contract for any codebase, language-neutral — the domain depends on nothing; the application depends only on ports it owns; adapters implement those ports and own the infrastructure types. Covers the five obligations, the file layout they imply (port at the package root, adapters beneath, factory apart), why a deferred import is a dependency cycle rather than a style choice, and why an absent collaborator is a null adapter rather than a nullable reference. Load for any service, app, or library with a domain worth protecting; code-structure covers structure inside a ring, and a stack skill (go-architecture, flutter-architecture, python-architecture, typescript-architecture) supplies the concrete mechanics. Applies to **/*.go,**/*.dart,**/*.ts,**/*.py."
metadata:
  generated_by: farrier
  source: library/skills/architecture/hexagonal-architecture/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-hexagonal-architecture/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [standards]
---

# The Ports-and-Adapters Contract

This is the language-neutral definition of what "hexagonal" (a.k.a. ports-and-adapters, a.k.a.
the dependency-inversion core of clean architecture) means. It states the *contract*; the concrete
mechanics of meeting it are stack-specific — pair this with the matching stack skill:

- Go → the `go-architecture` skill
- Flutter / Dart → the `flutter-architecture` skill
- Python → the `python-architecture` skill
- TypeScript / Node → the `typescript-architecture` skill

The point is not folder names. The point is that **business logic can be read, tested, and changed
without touching — or even knowing about — the database, the HTTP framework, or the cloud SDK.**
Machine-legibility (fast unit tests, swappable infrastructure, a dependency graph a tool can audit)
is a free consequence, not the reason. A core that names no infrastructure type is exactly a core a
fake can stand in for and a linter can prove clean. Do it because it keeps the domain honest; the
tooling falls out for free.

## The three rings

Every codebase this applies to has three concentric responsibilities. Name them however your stack
does — the *dependency direction* is what's load-bearing.

- **Domain** (innermost) — the entities and pure rules. Knows no I/O, no framework, no SDK. A
  domain type carries no persistence tag, no transport tag, no serialization annotation.
- **Application / services** — the use cases. Orchestrates domain logic and depends on **ports**:
  narrow interfaces, expressed in domain terms, that it *owns and declares*.
- **Adapters** (outermost) — the implementations: database repositories, HTTP handlers, queue
  clients, cloud SDK wrappers. Adapters implement ports and are the *only* place an infrastructure
  type may appear.

## The five obligations

Every codebase you touch owes all five. None is optional; none substitutes for another.

### 1. Dependencies point inward — always

The domain depends on nothing. The application depends only on the domain and on ports it declares.
Adapters depend on the application (to implement its ports) and on infrastructure. **The arrow never
reverses:** the core must not import, name, or reference a concrete adapter, an SDK, or a framework
type. If you find yourself importing the database package into a service to make it compile, the
design is inverted — introduce a port instead.

### 2. The domain is pure

A domain type is a plain value with no dependency on the outside world: no ORM/`firestore`/`json`
struct tags, no framework base class, no `@Entity`/`@Column`/serialization decorators. Persistence
and transport shapes live in the adapter ring as separate *entity* / *DTO* types, with an explicit
mapping to and from the domain type. When the domain and the wire format are the same struct, a
schema change and a business change become the same edit — that coupling is the thing this prevents.

### 3. Port purity — a port speaks only domain

**This is the invariant most often violated, and the one most worth enforcing.** A port is an
interface the *application* owns. Every method signature — parameters and returns — is expressed in
**domain terms only**. No infrastructure type may appear in a port signature: not a query builder,
not an HTTP request/response, not a SQL row or ORM session, not a cloud-SDK handle.

The subtle failure is a port that *looks* abstract but launders an infrastructure type through a
callback or a "flexible" parameter:

```text
# LEAK — the port names the driver's query type, so every caller and every
# fake is now coupled to that database. Swapping the store is now a core change.
port TodoRepository:
    Filter(criteria: <database>.Query) -> [Todo]        # ✗ infrastructure in a port
    DeleteBy(builder: (<database>.Collection) -> Query)  # ✗ a callback that speaks the driver

# CLEAN — criteria cross the boundary as a domain-level specification the adapter
# translates into a concrete query on the far side.
port TodoRepository:
    ListByOwner(ownerID: OwnerID) -> [Todo]              # ✓ domain vocabulary
    DeleteByOwner(ownerID: OwnerID) -> void              # ✓ intent, not mechanism
```

If a query is genuinely open-ended, model the criteria as a **domain specification** (a small value
object describing *what* to match) — never as a handle to the store's own query API.

The second failure mode is quieter and more common: a port that leaks nothing *specific* because it
names nothing at all. An untyped map, a free-form "any", a bare string standing in for a value with
structure — these pass a naive port-purity check while destroying the contract, because the shape
now lives in each adapter's head and no two agree.

```text
# LEAK — nothing here is checkable. Every implementer invents the item shape,
# the application re-derives it at each call site, and no fake can be wrong.
port WorkListStore:
    Load() -> [map<string, any>]                         # ✗ untyped payload
    Save(items: [map<string, any>]) -> void              # ✗ same
    RunTurn(prompt: string) -> string                    # ✗ "the outcome" as a string

# CLEAN — the domain types are the contract, and the outcome has a shape.
port WorkListStore:
    Load() -> [WorkItem]
    Save(items: [WorkItem]) -> void
    RunTurn(prompt: Prompt) -> TurnResult
```

**A port signature contains no `any`, no untyped map, and no primitive standing in for a
structured value.** The exception is a port that is *genuinely* generic over its payload — a cache,
a serializer, a transport. There the payload is a type parameter, not `any`.

### 4. A generic engine is not a contract

Reusable CRUD machinery is good and encouraged — one generic repository/gateway that any entity can
be bound to avoids reimplementing `get/set/update/list` per type. But **the generic engine must not
be the core's dependency.** The shape that satisfies both:

- a **generic adapter** (`Repository<Domain, Entity>` or equivalent) that holds the mechanics;
- a **narrow, domain-shaped port** the service depends on (obligation 3);
- a **thin binding** that instantiates the generic engine for one entity and exposes it *as* that
  port (a few lines: pick the collection/table, supply the domain↔entity mapping).

The service sees only the port. The generality lives entirely in the adapter ring, behind it.

**The mirror failure — a generic component that knows one of its consumers.** The same obligation
read from the other side: a component shared by N implementations must contain no code specific to
any one of them. When the first implementation is written *inside* the generic machinery and the
rest are bolted on beside it, the shared code silently becomes that one implementation's home, and
every other adapter is a second-class citizen implemented against a shape it cannot see.

**Trigger, stated so it can be checked:** in a module shared by N implementations, a symbol whose
name contains one implementation's name. `run_acme_cli`, `_acme_session_reset`, `parse_acme_event`
sitting in the generic driver are three findings, and grep finds all three.

**Fix.** Move the specifics behind the port with the others; what stays in the shared module is the
part every implementation genuinely uses. If the shared code needs a capability only one adapter
has today, add a **parameterised primitive** that names no adapter — the litmus test is *would a
different adapter want this unchanged?*

### 5. Purity is what makes the core testable

Because ports name only domain types, the application ring is exercised with an in-memory fake or a
generated mock and **no real infrastructure** — no database, no network, no emulator. If testing a
use case requires standing up the store, obligation 3 has been violated somewhere upstream; fix the
port, don't reach for the emulator. Fast, hermetic domain tests are the *proof* the boundary holds,
not a separate nicety.

## Layout — the port at the root, the adapters beneath it, the factory apart

The obligations above constrain *dependencies*; this constrains *files*, so that the dependency
graph is visible in a directory listing and a violation is hard to write by accident.

```text
backends/
  <port>            # the interface, and nothing else — imports none of its siblings
  claude            # one adapter per file
  codex
  copilot
  registry          # name → adapter; the ONLY module that imports every adapter
```

**Statement.** The interface lives at the package root; each implementation is a sibling module
beneath it; the interface module imports none of them.

**Trigger.** The module declaring the interface contains — or transitively imports — a concrete
implementation.

**Why the factory is a third file, not part of the port.** Something must map a name to a class,
and that something has to import every adapter. Put it beside the port and the port's module
transitively imports all of them, so the rule is broken by the very mechanism meant to serve it.
Three roles, three files: **the port declares, the adapters implement, the factory chooses.**

**Counter-case.** A single-implementation interface that exists as a test seam. One file is fine
until the second implementation arrives — the split is what the *second* one costs, not the first.

### A deferred import is a layering verdict, not an optimization

**Statement.** An import inside a function body is one of three things: a genuinely optional
dependency, a documented cold-start cost on a cold path, or **a dependency cycle**. In the third
case the fix is the dependency graph. Never the import.

**Trigger.** A function-scope import that executes on every call of a normal path.

**Fix.** Follow the cycle. One of the two modules is holding something that belongs to the other —
in practice, almost always a concrete adapter's code sitting inside the generic ring (obligation 4's
mirror failure, above). Move it out and both deferred imports disappear.

Deferred imports get filed as style. They are not: the language is reporting an inverted dependency,
and moving the import into the function body is how that report gets suppressed.

**Counter-case.** A dependency that is genuinely absent in a normal install — a test framework, an
optional extra — where the import failure is *handled* and the feature degrades. The handler is the
tell. If there is no handler, it was a cycle.

## An absent adapter is a null adapter, never a nullable reference

**Statement.** When a collaborator may be absent — telemetry off, cache unconfigured, notifier
disabled — represent absence with a **no-op implementation of the same port**. Not a nullable
reference checked at each call site, and never a name-keyed dispatch into a maybe-absent global.

**Trigger.** The same "if this is absent, return" guard appearing more than once for one
collaborator. Or: a call dispatched by looking a method up **by string name**.

**Fix.** Declare the port, ship a null implementation, and let the field's type be the port —
never nullable. Callers stop branching; the "do nothing" policy lives in exactly one class.

Name-keyed dispatch deserves the specific callout, because it usually arrives dressed as a
convenience facade and is strictly worse than the branching it replaced: it defeats every rename,
every signature check, and every find-usages. Combined with a swallow-all error policy, a typo in
the method name becomes a permanent silent no-op with nothing to notice it. Fail-soft is a fine
policy — implement it in one place, behind a typed interface, not by giving up on the type.

## How this is checked

Prose drifts; a contract that is only advice will be violated the first time a deadline is close.
Wherever your stack can, make these obligations **executable**, so a violation fails a gate instead
of surviving review:

- **Import direction** (obligations 1–2): assert the domain/application packages import no adapter
  or SDK package. Most stacks have a native way — Go build-tag/`depguard` lint, Dart layer-import
  lint, a Python import-linter contract, an ESLint `no-restricted-imports` boundary.
- **Port purity** (obligation 3): assert that no infrastructure symbol appears in an interface
  declared in the core — the single check that would have caught the leak above. The stack skill
  names the concrete matcher for its language.
- **Untyped port parameters** (obligation 3, second form): assert that no interface declared in the
  core names `any` or an untyped map. Most languages have a lint rule for a dynamically-typed
  annotation; scope it to interface declarations and it is a gate rather than a style warning.
- **One implementation named in generic code** (obligation 4, mirror failure): grep the shared
  module for each adapter's name. Zero hits is the bar.
- **Deferred imports**: grep for imports inside function bodies. Every hit needs a handled
  `ImportError`-equivalent beside it, or it is a cycle.
- **Missing seams** (obligation 5): grep the test suite for patches of private/internal names in
  production modules. Each one names a dependency that should have been injected.

A skill that states the contract *and* wires the check is the difference between architecture that
holds and architecture that erodes. When you add a port, you owe both.

## Scope boundary

This file governs the boundaries **between** rings. The rules for structure **inside** a ring —
when a group of functions becomes an object, when a module becomes two, when a value crossing a
boundary needs a name, where configuration and effects may appear — are in
[`../vcmi-mapgen-code-structure/SKILL.md`](../vcmi-mapgen-code-structure/SKILL.md). The two are
complementary: a codebase can satisfy every obligation here and still be unreadable, because
nothing above says a module may not be sixteen hundred lines.
