---
name: vcmi-mapgen-diagnosing-bugs
description: "Diagnosis discipline for a hard bug, a flake, or a performance regression — build a loop that goes red on this bug before forming any hypothesis, then reproduce and minimise, rank 3–5 falsifiable hypotheses, instrument one variable at a time behind a tagged prefix, and land the fix behind a regression test at a confirmed seam. Names the seams that exist here: pytest at the node seam by substitution, ostler doctor/graph/trace for a graph defect, groom status/logs/transcript/loops for what a live run actually did, workhorse control status for a process. Load when something is broken, throwing, failing intermittently or slower than it was, when a fix has already been attempted and did not hold, or when a coder run gives up on a QA gate."
metadata:
  generated_by: farrier
  source: library/skills/diagnosing-bugs/SKILL.md
  resolve: "farrier source .claude/skills/vcmi-mapgen-diagnosing-bugs/SKILL.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
  tags: [tests, standards, cli]
---

# Diagnosing bugs

A discipline for a bug that did not fall to the first look. Skip a phase only by saying which
one and why.

## Phase 1 — a loop that goes red

**This is the skill.** Everything after it is mechanical. With a **tight** pass/fail signal
that goes **red** on *this* bug, bisection, hypothesis-testing and instrumentation all just
consume it; without one, no amount of reading code substitutes. Spend disproportionate effort
here, and be relentless about it.

### The seams that exist in this repo

Reach for the highest one that can go red on the symptom — a loop closer to the bug is faster
and sharper than one further away.

| Seam | Command | Goes red on |
| --- | --- | --- |
| **Node** | `uv run pytest workflows/tests/<area> -q -k <node>` | a node's own logic — it is a plain function, so call it |
| **Package** | `uv run pytest <pkg>/tests -q` | anything inside one workspace member |
| **Graph** | `ostler doctor [--epic E] --json` | conformance and referential integrity; non-zero exit gates directly |
| **Graph, structural** | `ostler graph --orphans` / `--bullet` / `--path`, `ostler trace <id>` | a node nothing reaches, a duplicate grounding, a link that resolves to nothing |
| **QA obligations** | `ostler qa context --base REV --spec DIR --json`, then `ostler qa run` | a production change no obligation maps, or a plan that does not execute |
| **Doc shape** | `ostler fmt --check PATH` | a canonicalisation defect, with no writes |
| **Repo gates** | `make lint`, `make check-parsers`, `make check-no-env`, `make check-skills` | a standard the tree violates; exit status settles it |
| **A live run's history** | `groom logs --run <id>`, `groom transcript --run <id>`, `groom loops`, `groom profile` | what a node visit was actually told and said, which loop failed to converge, where the wall time went |
| **A live run, right now** | `groom status`, `workhorse-<name> control status --run <id>` | an unfinished node, which has no span — the trace cannot answer this |
| **CLI** | the command with a fixture input, diffed against a known-good output | an output regression |
| **Replay** | save the real payload, trace or event log to disk and drive the code path with it | a bug that only the production input triggers |
| **Throwaway harness** | a script under the scratchpad that calls the failing path directly | everything above being too far from the bug |

**Substitute, don't patch.** A node index and an agent backend are fields of the run, so a
test supplies its own rather than assigning over module attributes it then must restore — see
[[workhorse-scripting]]. A loop built on a patch that leaks is a loop that lies.

### Tighten the loop

Treat the loop as the product of this phase. Once you have *a* loop:

- **Faster** — narrow the test scope, skip unrelated setup, reuse a cached fixture.
- **Sharper** — assert the user's exact symptom, not "did not crash".
- **More deterministic** — pin time, seed randomness, isolate the filesystem, freeze the
  network. `Date`/`random`/live network are the three that make a loop flap.

A 30-second flaky loop is barely better than none; a 2-second deterministic one is a
superpower.

### An intermittent bug

The goal is not a clean repro but a **higher reproduction rate**. Loop the trigger 100×,
parallelise, add stress, narrow the timing window, inject a sleep at the suspected race. A
50%-flake is debuggable, a 1% flake is not — raise the rate until it is.

### Completion criterion — a red-capable command you have already run

Phase 1 is done when you can name **one command**, and have **already run it at least once**
showing its invocation and output, that is:

- **Red-capable** — it drives the actual bug code path and asserts the **reported symptom**,
  so it goes red now and green once fixed.
- **Deterministic** — the same verdict every run (or a pinned high reproduction rate).
- **Fast** — seconds, not minutes.
- **Runnable unattended** — no human in the middle.

Reading code to build a theory before that command exists is the exact failure this skill
prevents. No red-capable command, no Phase 2.

### When the loop genuinely cannot be built

Say so explicitly, list what you tried, and ask for one of: access to the environment that
reproduces it, a captured artifact (the run directory, `events.jsonl`, a HAR, a trace), or
permission to add temporary instrumentation to the running system. Redact every secret first
— write `<REDACTED>` in its place, and build loops against a variable so the credential stays
out of what you show. Proceeding to hypothesise without a loop is what this phase exists to
stop.

## Phase 2 — reproduce, then minimise

Run the loop and watch it go red. Confirm three things:

- It produces the failure **the report described**, not a different one nearby. Wrong bug,
  wrong fix.
- It reproduces across runs (or at a rate high enough to debug against).
- You have captured the exact symptom — message, wrong value, timing — so a later phase can
  prove the fix addressed *it*.

Then shrink to the **smallest scenario that still goes red**. Cut inputs, callers, config,
data and steps **one at a time**, re-running after each cut. Done when every remaining element
is load-bearing: removing any one turns the loop green.

A minimal repro shrinks the hypothesis space in Phase 3 and becomes the regression test in
Phase 5. Do not proceed until you have both reproduced and minimised.

## Phase 3 — hypothesise

Generate **3–5 ranked hypotheses before testing any of them**. Testing the first plausible one
anchors the whole diagnosis on it.

Each must be **falsifiable** — state the prediction: *if X is the cause, then changing Y makes
the bug disappear / changing Z makes it worse.* A hypothesis with no prediction is a vibe;
sharpen it or drop it.

Show the ranked list before testing. The person who reported it often re-ranks it instantly
("we deployed a change to #3 yesterday") or has already ruled one out. Do not block on the
answer — proceed with your own ranking if nobody is there.

## Phase 4 — instrument

Every probe maps to a specific prediction from Phase 3, and you **change one variable at a
time**.

1. Inspect state directly where the environment allows it — one breakpoint beats ten logs.
2. Otherwise log at the **boundary that distinguishes two hypotheses**, not everywhere.

**Tag every debug log with a unique prefix** — `[DEBUG-a4f2]` — so cleanup is one grep.
Untagged debug logs survive into the tree; tagged ones die.

**A performance regression takes the other branch.** Logs are usually the wrong instrument.
Establish a baseline measurement first — `groom profile` partitions a run's wall time and
separates workflow visits from backend retries, `groom cost` shows where the turns went, a
timer or a profiler for in-process work — then bisect against that number. Measure first, fix
second.

## Phase 5 — fix behind a regression test at a confirmed seam

Write the regression test **before the fix**, but only at a **correct seam** — one where the
test exercises the real bug pattern as it occurs at the call site. A seam too shallow to
replicate the chain that triggered the bug gives false confidence, which is worse than no
test.

**If no correct seam exists, that is itself the finding.** Say so. The architecture is what is
preventing the bug from being locked down, and that belongs in the post-mortem.

With a correct seam:

1. Turn the minimised repro into a failing test there.
2. Watch it fail.
3. Apply the fix.
4. Watch it pass.
5. Re-run the Phase 1 loop against the **original, un-minimised** scenario.

## Phase 6 — cleanup and post-mortem

Before calling it done:

- The original repro no longer reproduces (re-run the Phase 1 loop, not the minimised one).
- The regression test passes, or the absence of a seam is written down.
- Every `[DEBUG-…]` probe is gone — grep the prefix.
- Throwaway harnesses are deleted or left in the scratchpad, never in the tree.
- `make lint` and the affected package's tests pass.
- **The hypothesis that turned out correct is in the commit message**, so the next person
  diagnosing near this code starts where you finished.

Then ask what would have prevented it. If the answer is a gate this repo could have had, that
is a `make check` and a skill, not a note — see [[ostler-repo-docs]] and [[farrier-skills-writing]]. Make the
recommendation *after* the fix is in: you know more now than when you started.

## A coder QA `give_up` is a bug report, not a state

When a `coder` run gives up on a QA gate, the run has handed you a defect and stopped. It is
not self-recovery, and narrating it as recovery leaves the defect in the tree. It enters Phase
1 like any other bug, with the run's own artifacts as the starting evidence:

```bash
groom status                                   # which run, which node, how long it has been there
groom transcript --run <id>                    # what the node visit was told and what it answered
groom loops                                    # the review→rework lap that failed to converge
ostler qa context --base <rev> --spec <dir> --json   # the obligations the story actually owes
ostler qa validate <dir>/qa-plan.yml --spec <dir> --json
```

The red signal is usually already there: an obligation with no step, a plan that fails
validation, a scenario whose `qa/traces/<scenario>-diagnostics.json` carries the console error
or the 5xx nobody asserted on. Turn it into a command **you** can run — `ostler qa run` on the
repaired plan is a legitimate Phase 1 loop — before proposing a fix. A `give_up` that is
diagnosed and fixed is a bug closed; one that is narrated is a bug that comes back on the next
story.

## When to reach for the neighbours

- **The bug is in a workflow node** — the node contract, substitution-based testing, failure
  routing → [[workhorse-scripting]].
- **The bug is in the doc graph** — a dangling reference, an orphan seed, a `doctor` error code
  and its remedy → [[ostler-cli]] for the tool, [[ostler-okf]] for the format under
  `docs/features/`.
- **The evidence is in a run's telemetry** — spans, turn records, what each `groom` subcommand
  answers → [[groom-telemetry]].
- **The loop reads a structured document** — parse it, never regex it → [[structured-parsing]].

---

*The phase structure is adapted from [`mattpocock/skills`](https://github.com/mattpocock/skills)
(`diagnosing-bugs`), MIT-licensed, retargeted at this repo's seams.*
