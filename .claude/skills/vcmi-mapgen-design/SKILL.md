---
name: vcmi-mapgen-design
description: "The universal contract for a test that can actually fail, language-neutral — name the defect it catches, watch it go red before trusting it, derive fixtures from the producer instead of restating them, assert on evidence of work rather than status, and cover the composition and not only the units. Load whenever writing or reviewing tests; a stack skill (go-testing, python-testing, flutter-testing, react-router-qa, pulumi-qa) supplies the concrete mechanics, and the e2e-testing skill layers the end-to-end-specific contract (locators, waits, flake diagnosis) on top. Applies to test files in any language."
metadata:
  generated_by: farrier
  source: library/skills/testing/design/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-design/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [standards, tests]
---

# The Test-Design Contract

This is the language-neutral definition of what makes a test worth having. It states the
*contract*; the mechanics of meeting it — runner, assertion library, fixture syntax, mocking
tool — are stack-specific. Pair this with the matching stack skill:

- Go → the `go-testing` skill
- Python → the `python-testing` skill
- Flutter / Dart → the `flutter-testing` skill
- React Router / TypeScript → the `react-router-qa` skill
- End-to-end / journey specs (any driver) → the `e2e-testing` skill, plus the stack's driver mechanics (e.g. `react-router-playwright`)

**A green suite is not evidence. A suite that has failed for the right reason is.** Expensive
bugs rarely survive because nobody wrote a test; they survive because the tests that were
written could not detect them. Correct, well-named, fast, green — and blind. Every rule below
exists to make a test *capable of failing*.

## 1. Name the defect before writing the test

Complete the sentence **"this test fails when ______"** with a specific, plausible defect. If
the blank can only be filled with "the code changes" or "something breaks", you are writing a
change-detector, not a test. Delete it and write the one that names a real failure.

Put the answer in the test name or a one-line comment. A reader six months out needs to know
what the test is guarding, not what it calls.

## 2. See it red before you trust it

**Never trust a test you have not watched fail.** A test that has only ever been green is
untested itself — the assertion may not reach the code, the fixture may not exercise the path,
the mock may be absorbing the call.

- **Building new behavior → write the test first, full stop.** The test exists before the
  code it exercises, and it fails *because the behavior is missing* — not because an import
  is broken or a fixture won't load. Then write the code that turns it green. Test-after
  always degenerates into asserting what the code already does, which is §9's blind spot
  built in from day one.
- Fixing a bug → write the test **first**, watch it fail *for the reported reason*, then fix.
- Adding a test to existing code → break the code deliberately, confirm red, restore.
- Neither is possible → assert on a hand-built value that is wrong on purpose once, read the
  failure message, then correct it.

Red-first is not ceremony. It is the only evidence that the test is wired to the thing.
Keep the red run's output — a captured failing run is the proof a reviewer can check;
"I saw it fail" is not. A harness that separates the test-writing pass from the
implementing pass (as the coder workflow's red gate does) is this rule made structural:
the tests land alone, are *observed* red by deterministic tooling, and only then does the
implementation begin.

## 3. Derive fixtures from the producer; never restate them

When a fixture stands in for something the system generates — a scaffold, a schema, a
serialized payload, a config file — **generate it by calling the real producer**, not by
copying its output into the test file.

```
# wrong: a snapshot of what the scaffolder emitted, the day someone looked
FIXTURE = "## Context\n\n## Acceptance Criteria\n"

# right: the scaffolder itself, so the fixture cannot drift from the contract
def fixture(): return scaffolder.build_body(slug)
```

A hand-copied fixture stops being the truth at the exact moment the contract changes — which
is the moment the test was supposed to catch something. Drift is silent and the suite stays
green.

## 4. Test both sides of every predicate

For any boolean gate — `is_valid`, `is_done`, `has_access`, `should_retry` — assert the true
case *and* the false case, and be deliberate about which side is dangerous. A predicate that
wrongly answers **yes** skips work; one that wrongly answers **no** merely repeats it. The
first is almost always the costly direction, and it is the one usually left untested.

Give the predicate the input that is *most likely to fool it*, not the input that obviously
should pass. If "is this thing complete?" is answered by looking for a marker, the test that
matters is the one where an incomplete thing carries that marker.

## 5. Assert on evidence of work, not on status

`assert result.ok` passes when nothing happened at all. Status is the system's own opinion of
itself; a pipeline that performs zero work and reports success is the highest-severity class of
bug there is, and it satisfies every status assertion in the suite.

Assert on counts, contents, and side effects: *how many* records were written, *what* the output
actually contains, *which* steps executed. "Reported success" and "did the work" are different
claims and need different assertions.

## 6. Cover the composition, not only the units

A suite of isolated unit tests cannot see a **wiring** defect: the handler that is never
registered, the middleware left out of the chain, the pipeline stage the router skips, the
branch that routes past the step doing the real work. Every unit passes; the system does
nothing.

At least one test must run the **real composition** with the edges intact — the graph, the
router, the state machine, the DI container — and assert on *which steps executed*, not only on
the final value. Fake the expensive leaves (network, model calls, clock), never the wiring.

An end-to-end spec driving the running system through its UI is the outermost composition test,
and it carries failure modes of its own (locator brittleness, wait races, cold-start noise) — the
[`../vcmi-mapgen-e2e-testing/SKILL.md`](../vcmi-mapgen-e2e-testing/SKILL.md) skill is this
contract extended to that layer.

## 7. Every gate needs a test that proves it rejects

A validator, guard, or lint rule exercised only on valid input is not known to be a gate. For
each one, assert: valid input passes, invalid input **fails**, and the failure message *names
the offender*. A gate that fails without saying what failed sends the next person to read the
code instead of the output.

## 8. Never skip a suite to green

A test that skips when a dependency is absent reports success having exercised nothing — the
same silent degradation the tests exist to catch, relocated into the harness. If the code under
test genuinely requires a tool, declare that requirement so the environment fails loudly and
early; do not teach the suite to shrug.

Reserve skips for things that are legitimately not applicable (a platform-specific path on
another platform), never for "the thing this suite tests isn't installed".

## 9. Get ground truth from outside the code's own assumptions

Tests written from the same mental model as the implementation inherit its blind spots — they
can catch typos, never a wrong contract. If four places in a system agree on a wrong definition,
tests written alongside them agree too, and all of it is green.

Anchor assertions to something the implementation does not own: the spec, the upstream
producer's real output, a captured artifact from a real failed run, the consumer's actual
requirement. When a bug escapes to production, the artifact it produced belongs in the suite as
a fixture — that is ground truth no amount of shared assumption can bend.

## 10. Do not assert on incidentals

The counterweight to everything above. Do not pin whitespace, key order, log wording, or private
call sequences — a test that fails on a harmless refactor trains people to fix tests reflexively
rather than read them, and that habit is how a real failure gets waved through.

Assert the behaviour a caller depends on. If nobody would notice the change, no test should.

## Review checklist

Before approving a test — yours or someone else's:

- [ ] I can name the defect this fails on.
- [ ] It has been observed failing for that reason.
- [ ] Its fixtures come from the producer, not from a copy.
- [ ] It asserts work happened, not that status says so.
- [ ] The dangerous side of the predicate is the side under test.
- [ ] Something in the suite exercises the real wiring.
- [ ] It cannot skip itself into green.

## Smells

| Symptom | What it usually means |
| --- | --- |
| Suite green while a known bug is live | Fixtures encode the same assumption as the code (§3, §9) |
| Every test asserts `ok == true` | Status assertions, no evidence of work (§5) |
| A step "runs" but has zero executions in production | No composition test (§6) |
| A gate has only passing-input tests | It has never been shown to reject (§7) |
| Tests skip on the CI box, pass locally | Skipped-to-green suite (§8) |
| A refactor breaks 40 tests, no behaviour changed | Asserting incidentals (§10) |
