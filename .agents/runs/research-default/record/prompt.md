# Researcher — Record Result

The gate resolved. Persist the outcome per the PROGRESS protocol in
`hrnet-rules-authoring-workflow`. Do not change code or re-grade — only record.

Repository: `/mnt/data/workspace/vcmi-mapgen`
Program: `research`
Gate: `G0`
Progress log: `research/PROGRESS.md`
Forced outcome (if escalated): ``

## Do this

1. Determine the outcome: `PASS` / `WEAK_PASS` / `FAIL` / `KILLED` from the gate
   check (or the forced outcome if the rework cap was hit → `FAIL_MAX_REWORKS`,
   recorded as FAIL).
2. Fill the gate doc's **Result slot** (`research/<gate>.md`)
   — one line per metric, mean±std over seeds, deltas vs each control.
3. Update `research/PROGRESS.md`:
   - PASS/WEAK_PASS → status + one-line result + date.
   - FAIL/KILLED → status + one specific failure-mode line + which causes were
     tried/remain. **Never delete a failure entry.**
4. If a new architectural invariant emerged, append a dated entry to the program's
   RULES/notes.
5. If `KILLED` (or forced FAIL), write a negative-result finding under
   `research/findings/<gate>_<short>.md`.
6. Delete throwaway artifacts only; keep all learnings.

## Output (JSON only)

```json
{"record_result": {"status": "recorded", "outcome": "PASS", "progress_updated": true, "result_slot_updated": true, "finding_path": ""}}
```
