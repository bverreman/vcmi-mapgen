# HRNet Researcher — Record Result

The gate resolved. Persist the outcome per the PROGRESS protocol in
`hrnet-rules-authoring-workflow`. Do not change code or re-grade — only record.

Repository: the current repository (your working directory).
Gate: the resolved gate. (If the rework cap was hit, record the forced outcome
`FAIL_MAX_REWORKS` as a FAIL.)

## Do this

1. Determine the outcome: `PASS` / `WEAK_PASS` / `FAIL` / `KILLED` from the gate
   check (or use the forced outcome if the rework cap was hit →
   `FAIL_MAX_REWORKS`, recorded as FAIL).
2. Fill the gate doc's **Result slot** (`specs/hrnet/research/<gate>.md`) — one
   line per metric, mean±std over seeds, and the deltas vs each control.
3. Update `specs/hrnet/experiments/PROGRESS.md`:
   - PASS/WEAK_PASS → status + one-line result + date.
   - FAIL/KILLED → status + one specific failure-mode line + which causes were
     tried/remain. **Never delete a failure entry.**
4. Update the index row in `specs/hrnet/README.md` (fill the Result column).
5. If a new architectural invariant emerged, append a dated entry to
   `specs/hrnet/RULES.md`.
6. If `KILLED` (or forced FAIL), write a negative-result finding under
   `specs/hrnet/findings/<gate>_<short>.md` capturing what was tried and why the
   program (or gate) stops.
7. Delete throwaway artifacts only (per the spec's "What to delete on pass");
   keep all learnings.

## Output (JSON only)

```json
{"record_result": {"status": "recorded", "outcome": "PASS", "progress_updated": true, "index_updated": true, "finding_path": ""}}
```
