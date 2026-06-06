---
name: vcmi-mapgen-rules-authoring-workflow
description: "HRNet experiment authoring workflow — spec-before-code, file pairing, provenance headers, the PROGRESS.md write protocol, index updates, and archive-don't-delete. Load when creating specs or experiment code under specs/hrnet or src/hrnet."
---

# HRNet — Authoring Workflow (RULES.md, operationalized)

## Path & source-of-truth decisions (this agent)

- **Experiment code lives in `src/hrnet/experiments/<name>.py`** with a paired
  `test_<name>.py` in the same directory. The program of record (the P0–P4 docs)
  references `src/hrnet/`. Older docs (RULES.md, AGENTS.MD) mention
  `src/assembler/hrnet/` and a `<module>_test.py` naming — treat those as **stale
  for this program**. Do NOT stop-and-report on the `src/assembler/hrnet`
  package-boundary rule; use `src/hrnet/experiments/`.
- **The research program is authoritative for "what's next":**
  `specs/hrnet/research/` (README ladder + P0–P4). `roadmap_v5.md` is background
  context only, not the task driver.
- `specs/hrnet/experiments/PROGRESS.md` remains the live status log.

## Spec before code (always)

Before writing a non-trivial experiment, write/maintain
`specs/hrnet/experiments/<name>.md`. Use the AGENTS.MD spec format:

```text
# <name>
**Status:** IN PROGRESS | PASS | FAIL
**Date:** YYYY-MM-DD
**Depends on:** <files/gates this builds on>

## What this does        (what it tests, not how)
## Success gate          (exact numeric thresholds — copy from the P-doc)
## Result                (one line per metric, filled after running)
## Assumptions inherited (from upstream gates; resolve flagged ones first)
## What to delete on pass
```

If implementation changes the hypothesis, update the spec first.

## File hygiene

- One idea → one source file + one test file. Name after the concept.
- Experiment modules are independent: no cross-experiment imports. Duplicate a
  small utility locally rather than create a shared helper prematurely. Reuse of
  the *stable* shared primitives in `src/hrnet/` (`linear.py`, `vector.py`,
  `modules/`) is expected and encouraged — that is not a cross-experiment import.
- **Provenance header** at the top of every experiment file:

```python
# Origin: src/hrnet/linear.py   (or "original")
# Why imported: <one sentence — the local problem this solves>
# What changed: <removed/rewritten/added, or "unchanged">
# Benchmark before: <metric or N/A>
# Benchmark after: <fill after first run>
```

## PROGRESS.md write protocol (immediately on close, not end of session)

- **On PASS/WEAK_PASS:** mark status, record the result in one line (metric +
  key observation), record the date. If a new architectural constraint emerged,
  append a dated entry to `specs/hrnet/RULES.md`. Delete throwaway spec guides;
  keep specs that hold reusable decisions.
- **On FAIL/KILLED:** mark status, record the failure mode in one specific line
  (e.g. "prealigned ties random at k=0.5, Δsteps within noise"), record which
  causes were tried and which remain. **Never delete a failure entry** — and on a
  program-kill, write a negative-result finding under `specs/hrnet/findings/`.

## Index & archive

- When an experiment source file is added, add its row to the index table in
  `specs/hrnet/README.md` (leave the Result column empty until it has run).
- Retire, don't delete: move superseded/failed *code* to `src/hrnet/archive/` (or
  the area's archive) and record the outcome — but preserve the failure mode in
  docs first. Deletion of working-tree cruft is required (see
  `hrnet-never-constraints`); deletion of *learnings* is forbidden.
