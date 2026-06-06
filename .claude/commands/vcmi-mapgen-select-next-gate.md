# HRNet Researcher — Select Next Gate

You are the autonomous HRNet researcher. Decide which research gate to work on
next. The `hrnet-scientific-method-controls` and `hrnet-rules-authoring-workflow`
skills apply.

Repository: the current repository (your working directory).
Research program (authoritative): `specs/hrnet/research/`

## Do this

1. Read `specs/hrnet/research/README.md` — the P0→P4 ladder, dependencies, and
   the program kill criteria. This program is authoritative for "what's next";
   `roadmap_v5.md` is background only.
2. Read `specs/hrnet/experiments/PROGRESS.md` for the status of each gate.
3. Pick the lowest-numbered gate that is **not yet PASS/WEAK_PASS** and whose
   dependencies are satisfied (P0 blocks all; P1 needs P0; P2 needs P1 ≥ WEAK_PASS;
   P3 needs P2; P4 needs P3).
4. Check the kill criteria: if a prior gate's recorded result already trips a
   program kill criterion, the program is dead — set `program_killed: true`.
5. If every reachable gate is already PASS/WEAK_PASS, or the program is killed,
   return `gate_id: "none"`.

Do not implement anything in this step. Only read and decide.

## Output (JSON only)

```json
{"gate_selection": {"gate_id": "P0", "gate_doc_path": "specs/hrnet/research/P0_measurement_and_controls.md", "depends_on_satisfied": true, "program_killed": false, "rationale": "<one line>"}}
```
