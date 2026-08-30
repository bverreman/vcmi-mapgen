---
name: vcmi-mapgen-e2e-testing
description: "The end-to-end test contract, driver-neutral — locate by role plus a stable minimal accessible name, never a full literal string; rely on auto-retrying assertions instead of sleeps; recognize the cold-start first-navigation race as a named failure shape; run the flaky-vs-real checklist before calling a failure either one; keep every scenario isolated. Load whenever writing or reviewing an end-to-end/journey spec; a stack skill (react-router-playwright, flutter-qa) supplies the concrete driver mechanics. Applies to E2E spec files on any platform."
metadata:
  generated_by: farrier
  source: library/skills/testing/e2e-testing/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-e2e-testing/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [standards, tests]
---

# The E2E Test Contract

This is the driver-neutral definition of what makes an end-to-end spec trustworthy. It extends
[`generated testing-design instruction file when installed`](generated testing-design instruction file when installed) — every rule
there applies here too — with the failure modes unique to driving a real, running system through
its UI: locator brittleness, wait races, cold-start noise, and the "flaky or real?" judgment call.
The mechanics of meeting it — the driver API, the assertion syntax, the mock layer — are
stack-specific; pair this with the matching stack skill (e.g. `react-router-playwright` for a
React Router web app).

**An E2E failure is a claim about the product until proven otherwise, and a claim about the spec
until proven otherwise — in that order, with evidence for both.** A spec that fails for spec
reasons trains people to re-run and ignore it; a spec nobody trusts catches nothing.

## 1. Locate by role and a stable, minimal accessible name

The accessibility tree is the one queryable surface the product is *contractually obliged* to keep
stable — it is what assistive technology depends on. Locate through it, not around it:

- **Prefer role + accessible name** over text-content or CSS/structure locators. A CSS path pins
  the DOM shape; a role locator pins the user-facing contract. This is the same contract
  [`generated ui-accessibility instruction file when installed`](generated ui-accessibility instruction file when installed)
  requires the product to honor — an E2E locator is a screen-reader query, and a locator that
  cannot find an element is often reporting a real accessibility defect, not a test problem.
- **Match the name on a stable, minimal token — a substring or pattern, never the full literal
  sentence.** A full sentence shifts with copy edits, localization, and interpolated values, none
  of which are the behaviour under test. Pick the shortest fragment that uniquely identifies the
  element and cannot appear by accident.
- **When the full string genuinely is the assertion** (the story is about the wording), split it:
  first establish the element is present by role (with the retrying wait doing its work there),
  *then* assert the text content as a separate step. One compound "visible element with this exact
  400-character name" assertion couples a timing question to a content question, and when it times
  out you cannot tell which one failed.

## 2. Wait by retrying assertion, never by sleep

Use the framework's **auto-retrying assertions** — the ones that poll the condition until it holds
or a deadline passes — as the only wait mechanism. A manual sleep is wrong in both directions at
once: too short on a slow run (flake), too long on a fast one (waste), and it encodes a guess
about *how long* instead of a statement about *what must become true*.

When a longer deadline is legitimately needed — first navigation after a cold boot, a known-slow
external dependency — **extend it deliberately and say why** in a comment naming the slow thing.
An unexplained doubled timeout is the next reader's mystery and the next flake's hiding place; a
named one is a documented property of the system.

## 3. The cold-start / first-navigation race is a named failure shape

The first scenario to navigate after a freshly-booted dev server is a different environment from
every scenario after it: caches cold, bundles compiling on demand, and — decisively — many
frameworks' development mode **deliberately double-invokes first-mount effects**, so the initial
data fetch is issued, *aborted, and reissued* on first mount. A spec whose first assertion races
that abort/refetch cycle fails only there: first scenario, first run, fresh boot, and never again
on retry.

Recognize it in the diagnostics before rewriting anything: a **duplicate or aborted/reissued
request pair** for the same resource in the request log, an element snapshot that already shows
the expected state, and a failure that is exclusive to the first navigation. Write the entry
assertion so it survives the cycle: assert element presence by role first (the retrying wait
absorbs the refetch), assert content after, and give that first assertion the deliberate extended
deadline of §2 rather than a guessed bigger number sprinkled everywhere.

## 4. "Flaky" and "real" are both verdicts that need evidence

Before a repair declares an E2E failure a test defect — or a product bug — run this checklist.
It is [`generated testing-design instruction file when installed`](generated testing-design instruction file when installed) §9
(*ground truth from outside the code's own assumptions*) applied to E2E: the spec's own opinion of
what happened is not evidence; the captured artifacts are.

- [ ] **Does an isolated unit/component test make the identical assertion — and pass?** If the
  same role/name/state assertion is green at the component level, the component is correct and the
  E2E failure is about timing, environment, or locator scope.
- [ ] **Does the failure's own captured DOM/accessibility snapshot already contain the expected
  state?** A timeout whose final snapshot *shows the element it was waiting for* is a wait/locator
  defect by definition — the state arrived; the assertion couldn't see it or raced it.
- [ ] **Is there a request-log signature explaining a transient absence?** An aborted-and-reissued
  fetch pair (§3), a slow first compile, a 401→retry on token refresh — each names a window in
  which the UI legitimately lacked the state.
- [ ] **Does it reproduce on a warm re-run, in isolation, on a second machine?** First-run-only →
  cold start (§3). Suite-only → shared state (§5). Everywhere → believe it: it's the product.

A failure with none of these signatures is a product bug until shown otherwise — never "flaky" by
default. And the fix for a confirmed test defect is a *better locator or wait*, never a retry
loop around the scenario.

## 5. One scenario, one isolated world

No scenario may depend on another having run — not on its data, its authentication, its
navigation position, or its side effects. Fresh fixtures per scenario, own entry navigation,
own sign-in (or shared *setup machinery*, never shared *mutable state*). Ordering dependence is
the flake generator that looks most like product breakage: pass solo, fail in the suite, fail
differently under parallelism.

## 6. Evidence on failure; regression specs are committed, smoke checks are not

- Configure the run so every failure leaves **a screenshot/snapshot and a trace of requests and
  console output** captured *at the moment of failure*. §4's checklist is only runnable if the
  artifacts exist; a bare "timed out after 30s" forces re-running to even begin diagnosis.
- A **committed regression spec** guards a journey permanently: it must meet every rule in this
  file plus the testing-design contract (named defect, seen red, isolated). A **throwaway smoke
  check** — driving the app once to see a change render — is legitimate QA but is not a test; do
  not commit it as one. Which suite runs against mocks vs. the live stack, and with which flags,
  is the stack skill's territory.

## Smells

| Symptom | What it usually means |
| --- | --- |
| Assertion targets a full literal sentence | Brittle to copy/localization edits — minimal stable token instead (§1) |
| Locator is a CSS/XPath structure path | Pins DOM shape, not behaviour; also hides missing roles/names (§1) |
| A sleep with a magic number | A guess about "how long", not a statement of "what's true" (§2) |
| Timeout doubled with no comment | An undiagnosed race, papered over (§2, §3) |
| Fails only on first run after boot | Cold-start race, not a defect (§3) |
| Failure snapshot shows the awaited element | Wait/locator defect by definition (§4) |
| Passes solo, fails in suite | Shared state or ordering, not flakiness (§5) |
| Scenario wrapped in a retry loop | A confirmed defect being hidden instead of fixed (§4) |
