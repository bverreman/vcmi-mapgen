---
description: "Interview me relentlessly about a plan, a design or an idea until the design tree has no unvisited branch — one round of numbered questions per frontier, each with your recommendation"
argument-hint: "[the plan, decision or idea to stress-test]"
metadata:
  generated_by: farrier
  source: library/prompts/grill.md
  resolve: "farrier source .claude/commands/vcmi-mapgen-grill.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
---

# Grill

Stress-test this until we reach a shared understanding, not until you have enough to start:

$ARGUMENTS

Map it as a **design tree**: every decision branches into the decisions that hang off it. A
decision nobody has taken is a branch nobody has visited, and it gets taken silently later —
by you, at implementation time, without anyone noticing it was a decision.

## Work the tree in rounds

The **frontier** is every decision whose prerequisites are already settled — the questions
answerable **now**, without guessing at answers you have not heard yet.

Ask the **whole frontier in one round**, numbered, each with your recommended answer:

```
❓ **Q1** — **<question title>**: <the question, and the options if there are distinct ones>

➡️ <your recommendation, and the one-line reason it is your recommendation>
```

Then stop and wait. Each round of answers reshapes the tree: settled decisions push the
frontier outward and unblock questions that depended on them. Recompute the frontier and ask
the next round.

A question whose answer depends on another question still open **in this round** belongs to a
*later* round. Asking it now forces a guess at the prerequisite, and the guess is invisible in
the answer you get back.

## Facts are your job, decisions are mine

**Never ask me for something you could look up.** A frontier question that needs a fact from
the environment — what the code already does, which command exists, what a config says, how a
thing is done elsewhere in this tree — is a lookup, and you dispatch it. Read the file. Run
the command. Search the tree.

Do not block on it. A running lookup is an unsettled prerequisite, so only the questions
downstream of it wait — ask the rest of the frontier now, and fold the finding into the next
round.

The **decisions** are mine. Put each one to me and wait for the answer.

## Seed the frontier from what is already written down

Before the first round, look at what the repo already knows, so you are not asking me to
recite it:

- **An OKF graph, where one exists.** `ostler graph --surface <svc>` outlines the surface,
  `ostler trace <id>` walks from any node, `ostler list --type flow` gives the journeys, and
  `ostler graph --orphans` shows what nothing reaches. A node that exists is a settled
  decision; a node with a stub bullet, a dangling link or an orphan is a **frontier item**
  someone left open.
- **The planning graph.** `ostler doctor`, the epic's `## Seeds` and `## Stories`, an orphan
  seed no story covers — each is a branch already identified and not yet taken.
- **The repo's own rules** — `AGENTS.md`, the skills that apply to the area, the `make check`
  gates. A constraint already written down is not a question; a decision that would violate
  one *is* a question, and you raise it as such.

## Done

The session ends when the **frontier is empty** — every branch visited, nothing left silently
assumed. Then summarise the settled tree in one pass so I can see the whole shape at once.

Do not start implementing until I confirm we have reached shared understanding. If I ask you
to start early, name the branches still unvisited first, then do as I asked.

---

*Adapted from [`mattpocock/skills`](https://github.com/mattpocock/skills) (`grilling`),
MIT-licensed.*
