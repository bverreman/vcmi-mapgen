---
description: "Generate a wide field of genuinely distinct options for a problem, ground them in what the repo already knows, then converge to a ranked shortlist"
argument-hint: "[the problem, question or space to generate options for]"
metadata:
  generated_by: farrier
  source: library/prompts/brainstorm.md
  resolve: "farrier source .claude/commands/vcmi-mapgen-brainstorm.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
---

# Brainstorm

Generate options, don't settle one, for:

$ARGUMENTS

Where `grill` narrows a plan you already have, this widens a space you don't yet have a
plan for. Nothing here is a decision until I pick one.

## Ground before diverging

Before generating anything, look at what the repo already knows, the same way `grill`
seeds its frontier — so you are not proposing what already exists or what a written
constraint already rules out:

- **Prior art in the tree.** Existing skills, commands, modules or docs that already
  cover part of this space — grep for it, don't guess.
- **An OKF graph, where one exists.** `ostler graph --surface <svc>`, `ostler list --type
  flow`, `ostler graph --orphans` — what the domain already models is not a fresh idea.
- **The repo's own rules** — `AGENTS.md`, the skills that apply to the area. A constraint
  already written down narrows the space; note it rather than generating options it
  rules out.

Do this yourself. Don't ask me for a fact you could look up.

## Diverge

Produce as many **genuinely distinct** options as the problem supports — not phrasings or
minor variations of one idea. The first two or three are what anyone would think of
first; they're the floor, not the output. Keep going past them: change the axis (a
different mechanism, a different scope, a different owner of the complexity, the
opposite of the obvious default) rather than refining the same one twice.

Name each option in a line or two — no need to develop it yet. Bad or half-broken options
belong in the list too if they illuminate the space; cut them at the converge step, not
before.

## Converge

Group near-duplicates, drop options strictly dominated by another (worse on every axis,
better on none), and present a **ranked shortlist (3-6)**:

```
**<option name>** — <one line: what it is>
   <one line: the main tradeoff, and who/what it's good or bad for>
```

Rank by fit to the stated problem, not by familiarity — the obvious option is not
automatically first.

Then stop and wait. I'll pick one, ask you to develop two of them further, or send you
back to diverge again from a direction that looked promising. A pick here is the start of
a decision, not the end of one — hand a chosen option to `grill` to work out its shape.

## Done

The session ends when I pick a direction or say stop — there's no frontier to empty here,
since divergence doesn't converge on its own. Don't start implementing a chosen option
without confirming that's what I want; a brainstorm answers "what could this be," not
"build it."
