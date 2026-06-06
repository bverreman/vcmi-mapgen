# Research Lead — Revive a Wrongly-Killed Gate

The research lead judged gate `{{ gate_id }}` of the program at `{{ program_dir }}`
to have been killed for the **wrong reason** (an apparatus artifact, not a refuted
hypothesis). Withdraw the kill and submit the gate back for another pass. Follow
the PROGRESS protocol in `hrnet-rules-authoring-workflow`: **never delete a failure
entry — supersede it.**

Repository: `{{ repo_dir | default('.') }}`
Program: `{{ program_dir }}`
Progress log: `{{ progress_path }}`
Gate: `{{ gate_id }}`
Gate doc: `{{ gate_doc_path }}`
Lead's verdict / required fix:

```json
{{ lead_review | default('{}') }}
```

## Do this

1. Write a reassessment finding at
   `{{ program_dir }}/findings/{{ gate_id }}_kill_reassessment.md` stating that the
   kill was a measurement/apparatus artifact, the evidence (`evidence` above), and
   the corrective (`apparatus_fix` above). Leave the original negative-result
   finding in place, unchanged.
2. In `{{ progress_path }}`: change the gate's status from `KILLED` to `REOPENED`
   with a one-line reason, set its dependents back to `NOT STARTED (pending {{ gate_id }})`,
   and add a dated "Reopening" note. Keep the original KILLED entry verbatim as the
   record of the first attempt.
3. In the gate doc `{{ gate_doc_path }}`: set status to REOPENED, keep the prior
   Result slot as "first attempt", and add a corrective-plan section that bakes in
   the lead's `apparatus_fix` so the next implementation pass cannot repeat the
   artifact (e.g. swap the broken substrate, run the real controls, wire the P0
   harness, make the leak guard honest).
4. Do **not** implement the experiment or re-grade — the researcher loop will pick
   the REOPENED gate up next and run it. Only re-open and re-scope.

## Output (JSON only)

```json
{"revive_result": {"status": "reopened", "gate_id": "{{ gate_id }}", "finding_path": "{{ program_dir }}/findings/{{ gate_id }}_kill_reassessment.md", "progress_updated": true, "gate_doc_rescoped": true}}
```
