# Researcher — Record Result

The gate resolved. Persist the outcome per the PROGRESS protocol in
`hrnet-rules-authoring-workflow`. Do not change code or re-grade — only record.

Repository: `{{ repo_dir | default('.') }}`
Program: `{{ program_dir }}`
Gate: `{{ gate_id }}`
Progress log: `{{ progress_path }}`
Forced outcome (if escalated): `{{ forced_outcome | default('') }}`

## Do this

1. Determine the outcome: `PASS` / `WEAK_PASS` / `FAIL` / `KILLED` from the gate
   check (or the forced outcome if the rework cap was hit → `FAIL_MAX_REWORKS`,
   recorded as FAIL).
2. Fill the gate doc's **Result slot** (`{{ gate_doc_path | default(program_dir + '/<gate>.md') }}`)
   — one line per metric, mean±std over seeds, deltas vs each control.
3. Update `{{ progress_path }}`:
   - PASS/WEAK_PASS → status + one-line result + date.
   - FAIL/KILLED → status + one specific failure-mode line + which causes were
     tried/remain. **Never delete a failure entry.**
4. If a new architectural invariant emerged, append a dated entry to the program's
   RULES/notes.
5. If `KILLED` (or forced FAIL), write a negative-result finding under
   `{{ program_dir }}/findings/<gate>_<short>.md`.
6. Delete throwaway artifacts only; keep all learnings.

## Output (JSON only)

```json
{"record_result": {"status": "recorded", "outcome": "PASS", "progress_updated": true, "result_slot_updated": true, "finding_path": ""}}
```
