---
description: "Fold this conversation's durable learnings back into the agent library — skills, prompts/commands, and root instruction files"
metadata:
  generated_by: farrier
  source: library/prompts/agent-library/update-skill-from-conversation.md
  resolve: "farrier source .claude/commands/vcmi-mapgen-update-skill-from-conversation.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
---

# Improve the Agent Library From This Conversation

Capture the durable, generalizable learnings from **this conversation** and fold
them into the shared agent library so the next agent starts with them already
known. This one command covers every shape the library ships:

- **skills** — behavioural rules and knowledge an agent loads by file scope
  (`library/skills/<group>/<name>/`).
- **prompts / commands** — repeatable task procedures a human invokes as a slash
  command (`library/prompts/<group>/<name>.md`, rendered to `.claude/commands/`).
- **root instructions** — the always-on `AGENTS.md` / `CLAUDE.md` an agent reads
  first (generated from skills, or hand-authored — see step 3).

Improving is not only adding: **refactor** a bloated skill, **split** one that
grew two jobs, **retire** a rule that stopped being true, or **add** a new skill
or command when nothing fits. Reuse before you create.

The library is the single source of truth. Never hand-edit a generated adapter
(`.claude/`, `.codex/`, `.github/`, `.agents/`, or a generated local
`AGENTS.md`/`CLAUDE.md`) — change the library, then re-install.

This repo does not ship the agent-library skill, so the essentials are inlined
below. If you have `farrier` installed, `farrier config show` documents the same
layout for the configured library.

## 0. Locate the library — never hardcode its path

The library's filesystem location differs per machine. Resolve it, don't guess:

```bash
farrier config show library_dir     # the overlay library CONTENT root
```

Everything below is relative to that directory (`<library_dir>/library/skills/…`,
`<library_dir>/library/prompts/…`, `<library_dir>/packs/…`). If `farrier` is not
configured, ask the user where the library lives rather than assuming.

## 1. Mine the conversation for what's worth keeping

Re-read the conversation and extract only **durable** knowledge — things that will
be true for the *next* task, not facts specific to this one. Good candidates:

- A correction the user made to how you worked, and the reason behind it.
- A convention, gotcha, or invariant you discovered that wasn't written down.
- A command, path, or workflow that turned out to be the right one (or the wrong
  one to avoid).
- A pattern confirmed to work after trial and error.
- A recurring multi-step task you performed by hand that deserves to be a
  reusable **command** so it runs the same way every time.

Discard anything one-off (a specific bug fixed, a file moved, a value that only
mattered today). If nothing durable came out of the conversation, say so and
stop — do not invent guidance to justify an edit.

**State the 1–5 learnings you intend to record, and where each will land, before
you edit**, so the user can correct you.

## 2. For each learning, pick the right shape and home — reuse before create

First decide *what kind* of artifact the learning is:

| The learning is… | It belongs in a… |
| --- | --- |
| a rule/knowledge an agent should apply while working in a scope | **skill** |
| a procedure a human will deliberately re-run start to finish | **prompt / command** |
| something every agent must know before any task in a repo | **root instruction** (step 3) |

Then find its home, preferring an **edit** over a new file:

- **Skills** — search `library/skills/<group>/` for a skill whose scope
  (`applyTo` / `name` domain) already covers the area. Generic knowledge lives in
  the generic group (`ui`, `infra`, `qa`, `review`, `process`, `stacks/<stack>`,
  `research`); repo-specific deltas live in `projects/<repo>/` and must **not**
  restate a generic skill — cross-link it instead with
  `{{ instruction_file("<name>") }}`. Follow the
  reuse-before-create decision tree in `library/skills/CLAUDE.md`.
- **Commands** — search `library/prompts/<group>/` for a prompt that already does
  the task. Extend it rather than adding a near-duplicate (two commands doing
  almost the same thing is the indirection the pack policy warns against).
- Add a **new** skill or command only when nothing fits. Name it with a
  library-unique domain token; a skill's directory name must equal its `name:`.

**Refactoring counts as improvement.** If the right move is to split an
overgrown skill, merge two that overlap, tighten prose, or delete a rule the
conversation proved wrong — do that, and update every pack and cross-link that
referenced the old shape in the same change.

## 3. Updating a root AGENTS.md / CLAUDE.md correctly

A repo's root instruction file is one of two things — check before editing:

1. **Generated by farrier** — it carries a frontmatter marker
   (`generated_by: farrier`, `do_not_edit: …`, or a `resolve:` line). **Never
   edit it directly.** It is composed from selected skills. Put the learning in
   the underlying skill (its frontmatter's `resolve`/`source` line names the
   source), or adjust the repo's `localInstructions` / `roots` selection, then
   re-install (step 5). The generated file updates itself.
2. **Hand-authored** — a plain root file the repo maintains itself (no farrier
   marker; e.g. a public repo's own `CLAUDE.md`). Edit it directly, and note in
   your report that this edit is outside the library and won't propagate to
   other repos.

If unsure which, `grep -l 'generated_by: farrier' AGENTS.md CLAUDE.md` — a match
means route through the library.

## 4. Make the smallest correct edit

- Write learnings as **imperative rules**, not a story about today ("Bound every
  command with a `timeout`", not "Today a command hung").
- Match the file's existing structure, tone, and heading style. Add to the
  relevant section; don't append a loose paragraph.
- Keep `applyTo` frontmatter accurate if a skill's scope changed.
- **Wire new artifacts into a pack.** A new skill/command/prompt is only
  installed if a pack a repo selects lists it (`skills:` as `<group>/<name>`,
  `prompts:` as `<group>/<name>`), or the repo selects it directly in its
  top-level `skills:` / `prompts:`. Follow `packs/CLAUDE.md`: no single-entry
  packs, no project packs.

## 5. Re-install and verify — in every consuming repo

The library change is inert until each consuming repo regenerates its adapters.
From each affected repo root (every command bounded by a wall-clock `timeout`):

```bash
timeout 300 make agent-install     # regenerate .claude/ (+ any enabled adapter)
timeout 120 make agent-check       # verify no drift
```

Before calling the work done:

1. `make agent-check` passes (no drift) in each repo you touched.
2. The changed skill/command text appears in that repo's generated `.claude/`
   output (a command shows up as `.claude/commands/<name>.md`).
3. A pack (or direct selection) actually carries the change into that repo —
   otherwise the install is a no-op there.
4. If a touched repo is **public**, confirm the change is public-clean (no
   private overlay or client name leaked into the rendered adapter) — run its
   `make check-public` if it has one.
5. Stage the library change **and** the regenerated adapter files together.

## 6. Report

Summarize what you recorded: each learning, the file it landed in (skill /
command / root), whether it was an edit, a refactor, or a new artifact, and which
repos you re-installed. Note anything you judged too one-off to keep, and any
hand-authored root you edited directly.
