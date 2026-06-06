# Research Lead — Define a New Research Direction

The research lead judged gate `{{ gate_id }}` of the program at `{{ program_dir }}`
to have been killed for the **right reason**: under a faithful implementation with
real controls and honest guards, the gate's hypothesis was genuinely refuted. The
current program framing cannot clear it. Your job now is to define a **new research
direction** that still serves the standing goal — not to abandon the goal.

> **Standing goal (unchanged):** train a **cheaper LLM agent** — an
> **English→Python** agent — by determining how a network can be **pre-aligned and
> segmented** to make training **fast on limited hardware (one modest machine).**

The `hrnet-scientific-method-controls`, `hrnet-never-constraints`, and
`hrnet-rules-authoring-workflow` skills define the discipline. Negative results are
**inputs**, never deleted: the refuted hypothesis tells you what NOT to try again.

Repository: `{{ repo_dir | default('.') }}`
Program: `{{ program_dir }}`
Progress log: `{{ progress_path }}`
Refuted gate: `{{ gate_id }}` (doc: `{{ gate_doc_path }}`)
Lead's review:

```json
{{ lead_review | default('{}') }}
```

## Do this

1. Read the program README, every recorded finding under
   `{{ program_dir }}/findings/`, and `{{ progress_path }}`. Enumerate what the
   accumulated negative results now **rule out** (e.g. HRNet: knowledge pinned into
   weights does not generalize to unseen identifiers; this program: `{{ gate_id }}`'s
   refuted hypothesis).
2. Propose a new direction that (a) still targets the standing goal — cheaper
   training of an English→Python agent via pre-alignment/segmentation on limited
   hardware — and (b) does not repeat any ruled-out approach. Make the core
   question sharp and falsifiable, and front-load the cheapest experiment that
   could kill it.
3. Record it as a real program, reusing the existing scaffolding:
   - Update `{{ program_dir }}/README.md` (or write a successor README and link the
     old one as `Supersedes:`): new core question, the two/few modules, the shared
     controls, shared metrics, the anti-shortcut/leak guards, a fresh **gate ladder
     (G0…Gn)** with explicit dependencies and **program kill criteria**, and a "what
     we learned / what is ruled out" section citing the prior findings.
   - Create the new gate docs (`G0_*.md` … ) each with: hypothesis, design, exact
     numeric success gate, controls, and an empty Result slot — mirroring the
     existing gate-doc format so the researcher loop can consume them.
   - Reset `{{ progress_path }}` to the new ladder: all new gates `NOT STARTED`,
     while **preserving** the prior program's status table and failure analysis in a
     clearly-labelled "Previous direction (superseded)" section. The new ladder must
     be what `select-next-gate` reads first, and the old killed gates must NOT
     re-trip a program-kill for the new direction.
4. Keep the reusable machinery: the P0 measurement harness, the control factory,
   multi-seed/CV discipline, and the leak/zero-weights guards carry over. Do not
   rebuild what already works; do not weaken any NEVER constraint.
5. Do **not** implement experiments here — only define and record the direction.
   The researcher loop will select `G0` of the new ladder next.

## Output (JSON only)

```json
{"new_direction_result": {"status": "defined", "supersedes_gate": "{{ gate_id }}", "direction_name": "<short name>", "core_question": "<one line, falsifiable>", "ruled_out": ["<prior dead end>"], "new_gates": ["G0", "G1"], "readme_path": "{{ program_dir }}/README.md", "progress_reset": true}}
```
