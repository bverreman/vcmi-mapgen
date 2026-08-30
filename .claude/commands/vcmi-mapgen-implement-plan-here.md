---
description: "Take a plan as the goal and implement it right here — on the branch already checked out, in this checkout, with no worktree and no branch switch"
argument-hint: "<path to the plan>"
metadata:
  generated_by: farrier
  source: library/prompts/implement-plan-here.md
  resolve: "farrier source .claude/commands/vcmi-mapgen-implement-plan-here.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
---

# Implement a plan, here, on the branch you are on

The plan below is the goal for this session, and this checkout is where it gets built.
No worktree, no new branch, no `git switch` — the work lands on whatever branch is
currently checked out, which is a decision the operator already made by checking it out.

$ARGUMENTS

## 1. Make the plan the goal

Read the plan named above. If the argument is a path, read the file; if it is prose,
that prose is the plan. Then, before touching anything, write down for yourself:

- **the goal** — one sentence saying what is true when this is done;
- **the definition of done** — the checks that decide it: the tests that must pass, the
  gate this repo runs (the command its README or Makefile names),
  and any behaviour you must actually observe rather than infer;
- **the order** — the plan's steps as you will do them, with anything the plan left
  ambiguous called out now rather than discovered at step six.

If the plan is underspecified in a way that changes what you would build, ask **now**,
before the first edit. If it is merely thin, state the assumption you are proceeding
under and keep going — do not stall on a question you can answer yourself.

The plan is the scope. Do not widen it because you noticed something adjacent, and do
not narrow it because a step looks hard.

## 2. Find out who else is in this tree

Working in place means you are not alone in it. A concurrent agent, a background run, or
the human may own some of these paths, and there is no fresh checkout insulating you
from them.

```bash
git branch --show-current            # where the work is about to land
git status --porcelain               # what is already uncommitted, and whose
git log --oneline -5                 # what this branch is
```

Read that output before editing:

- **Anything already uncommitted is not yours.** Do not commit it, do not revert it, do
  not stash it — a stash makes someone else's work vanish from *their* `git status`.
  Work around it, and say so if it genuinely blocks you.
- **Check the branch is the one the plan belongs on.** If the plan should land somewhere
  else, say so and stop — switching branches under a dirty tree is exactly the move this
  variant exists to avoid. Cutting a worktree is the other variant's job.
- **If a long-running process is reading these files** — a workflow run, a dev server, a
  watcher — know that your edits reach it mid-flight, and say what you are about to
  touch.

## 3. Take the baseline before you change anything

```bash
# the gate this repo names in its README
```

On the unmodified tree, now. A green baseline is what makes the first red result mean
something. If the baseline is already red, say so and decide deliberately whether to
proceed — you cannot otherwise tell your breakage from the one that was already there.

## 4. Work the plan

Implement it step by step, in the order you wrote down.

As each concern becomes complete, commit it on this branch — **one concern per commit,
staged by explicit path**, subject in the repo's convention. Staging by path is not
tidiness here; it is the only thing keeping a `git add -A` from sweeping up the
uncommitted work you found in step 2. That is the commit procedure already resident in
this repo's root instructions, and it applies here unchanged, including how to push:
over HTTPS with `gh` holding the credential, never the remote's `git@` URL.

Push as you go, right after each commit. A local commit is invisible to review and to
CI, and it dies with the machine.

Verify against the definition of done from step 1 — the real checks, not a plausible
argument that it should work. When a step turns out to be blocked, finish everything
that does not depend on it and say plainly which part you left and why.

## 5. Hand back

When the plan is done, report: the branch, the commits you landed, whether they are
pushed, what the gate says, and anything the plan asked for that you did not do. Report
separately anything still uncommitted in the tree, saying which of it is yours — after
working in place, that distinction is the operator's next decision and only you can
still make it.
