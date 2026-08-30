---
name: vcmi-mapgen-vertical-slicing
description: "Tracer-bullet story slicing — the first story is a walking skeleton (the thinnest end-to-end path through every layer, observable in the running system), and every later story widens that path; no story is a horizontal layer. Load when splitting an epic into stories or reviewing a split."
metadata:
  generated_by: farrier
  source: library/skills/vertical-slicing/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-vertical-slicing/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [planning]
---

# Vertical slicing — tracer bullets, not layers

A story earns its place by moving a **user-observable journey step** through the *whole*
stack — whatever "whole" means for this system: UI to API to storage, CLI to engine to
disk, ingest to model to report. A story that builds one layer for many future steps
("the data model", "all the endpoints", "the component library") is a **horizontal**
slice: it cannot be QA-ed at the running-system boundary, its integration risk is
deferred to whichever story finally connects the layers, and until that story lands the
epic has spent most of its budget with nothing an actor can do.

## The first story is the walking skeleton

The first story in every epic — the dependency-root the other stories build on — is the
**tracer bullet**: the thinnest path that lets the epic's actor complete *one* real step
of the journey end to end, however small, in the running system.

- **Thinnest** means minimum width, full depth. One screen with one control, one
  endpoint, one table, one happy path — but genuinely wired: the control calls the real
  endpoint, the endpoint hits the real store, and the result is observable where the
  actor stands. Hard-coded breadth is fine; a mocked layer is not — a skeleton with a
  fake spine proves nothing about integration, which is the one thing it exists to prove.
- It carries the unavoidable setup (project scaffold, hosting, schema baseline, CI)
  *as ride-along work inside itself*, sized to what this one path needs — not as
  preceding enabler stories. If the setup feels too big to ride along, the skeleton is
  too wide; narrow the path, not the depth.
- Its acceptance criterion is the actor completing that step at the running-system
  boundary, same as any other story.

## Every later story widens the skeleton

Sequence stories so each one **widens** a path that already runs — another journey step,
another entity, the error paths, the polish — and ends with the journey demonstrably
longer or richer than the story before it. The test for a proposed story:

> *After this story is green, what can the actor do that they could not do before?*

If the honest answer is "nothing yet — but the next story will be easier", it is a
horizontal slice wearing a story's clothes. Fold it into the earliest story whose
journey step actually needs it.

## What this forbids, concretely

- A first story named "set up the backend", "create the data model", "scaffold the
  frontend" with no journey step of its own.
- A split ordered by layer — all storage stories, then all API stories, then all UI
  stories — where the first end-to-end moment arrives in the final third.
- A dependency edge that exists only because "the layer below should be built first".
  A story depends on another when it widens a path that story opened, not because of
  architectural strata.
- Deferring integration: any story whose deliverable runs only in a test harness or
  behind a mock of an adjacent layer that a sibling story builds for real.

## What this does *not* demand

- Not every story touches every layer — a widening story may live mostly in one layer
  when that is where the width is. The rule is that each story ends at an observable
  journey step, not that each story is uniformly deep.
- Technical enablers are still allowed under the standing rule: only when they directly
  unlock a named journey step and have an externally observable boundary. Vertical
  slicing sharpens that rule; it does not replace it.
- The skeleton need not be pretty. Unstyled, unvalidated, single-user is fine — width
  comes later, by design.
