---
description: "Take a plan as the goal, cut a git worktree for it under the machine's configured worktree directory, branch there, and start implementing — leaving the current checkout untouched"
argument-hint: "<path to the plan> [branch name]"
metadata:
  generated_by: farrier
  source: library/prompts/implement-plan-worktree.md
  resolve: "farrier source .claude/commands/vcmi-mapgen-implement-plan-worktree.md"
  do_not_edit: "generated — run the `resolve` command below for this machine's editable source path, edit that, then `make agent-install` to regenerate"
---

# Implement a plan, on a branch, in its own worktree

The plan below is the goal for this session. Everything after it is about giving that
work its own checkout first, so the tree you are sitting in — which may hold someone
else's uncommitted edits, or a run reading its files — is not where the plan gets built.

$ARGUMENTS

## 1. Make the plan the goal

Read the plan named above. If the argument is a path, read the file; if it is prose,
that prose is the plan. Then, before touching git, write down for yourself:

- **the goal** — one sentence saying what is true when this is done;
- **the definition of done** — the checks that decide it: the tests that must pass, the
  gate this repo runs (the command its README or Makefile names),
  and any behaviour you must actually observe rather than infer;
- **the order** — the plan's steps as you will do them, with anything the plan left
  ambiguous called out now rather than discovered at step six.

If the plan is underspecified in a way that changes what you would build, ask **now**,
before the worktree exists. A wrong branch is cheap; a wrong branch with four commits on
it is not. If it is merely thin, state the assumption you are proceeding under and keep
going — do not stall on a question you can answer yourself.

The plan is the scope. Do not widen it because you noticed something adjacent, and do
not narrow it because a step looks hard.

## 2. Find where worktrees go on this machine

```bash
farrier config show worktree_dir
```

That key is machine-local disk layout — one machine keeps worktrees on a data volume,
another under `~` — which is exactly why it lives in `~/.config/stablemate/config.toml`
and not in any repo's instructions.

If it is unset, the command exits non-zero and says so. Do not invent a location and do
not scatter the worktree next to the checkout: tell the operator to set it once, and
offer the command.

```bash
farrier config set-worktree /path/to/worktrees   # e.g. a big data volume
```

## 3. Cut the worktree and the branch in one step

```bash
cd <the repo>
git rev-parse --show-toplevel        # the repo you are branching from
git status --porcelain               # what is uncommitted here
```

**Uncommitted work does not come with you.** A worktree is a fresh checkout of a commit,
so anything dirty in the current tree stays behind. If the plan builds on those edits,
commit them first, the way this repo's commit rules say to — do not stash, which hides
them from the person who owns them.

Then cut it. One command creates the directory, the branch, and the checkout:

```bash
git worktree add -b <branch> "$(farrier config show worktree_dir)/<repo>-<slug>" <base>
```

- `<repo>-<slug>` — the repo name and a short kebab-case slug from the plan, e.g.
  `acme-worktree-config`. The repo name is not decoration: one worktree directory holds
  worktrees from every repo on the machine, and `refactor-auth` alone tells you nothing
  six weeks later.
- `<branch>` — the same slug, prefixed the way this repo prefixes branches if it does.
  `git worktree add -b` fails loudly if the branch already exists, which is the answer
  you want.
- `<base>` — the commit to branch from, and name it explicitly. `origin/main` after a
  `git fetch` when the plan should land on the shared tip; the current `HEAD` when it
  depends on work not pushed yet. Defaulting silently is how a plan gets built on a
  week-old tree.

## 4. Make the worktree usable before you start

A worktree shares the repo's history and nothing else. Untracked, gitignored setup — the
virtualenv, `.env`, local caches, build output — is **not** there, so the first command
that needs any of it fails in a way that looks like your change broke something.

```bash
cd <the new worktree>
make install   # or whatever this repo's bootstrap is — read its README
```

Run the repo's gate once, now, on an unmodified tree. A green baseline is what makes the
first red result mean something. If the baseline is already red, say so and stop — you
cannot tell your breakage from the one that was already there.

From here on, **every** command runs in the worktree. Confirm it once
(`git rev-parse --show-toplevel && git branch --show-current`) and do not edit files
under the original checkout by absolute path — it is the same repo, and nothing will
warn you.

## 5. Work the plan

Implement it step by step, in the order you wrote down. As each concern becomes
complete, commit it on this branch — one concern per commit, staged by explicit path,
subject in the repo's convention. That is the commit procedure already resident in this
repo's root instructions, and it applies here unchanged.

Push the branch when the first commit lands, then keep pushing as you go. A branch that
exists only in a worktree on one machine is invisible to review and to CI, and it dies
with the machine. Push the way those instructions say to — over HTTPS with `gh` holding
the credential, never the remote's `git@` URL, which is the human's SSH key and hangs on
a passphrase prompt no agent can answer.

Verify against the definition of done from step 1 — the real checks, not a plausible
argument that it should work. When a step turns out to be blocked, finish everything
that does not depend on it and say plainly which part you left and why.

## 6. Hand back

When the plan is done, report: the worktree path, the branch, whether it is pushed, what
the gate says, and anything the plan asked for that you did not do. Leave the worktree in
place — it is where the review happens. Removing it is the operator's call:

```bash
git worktree remove <path>          # only when asked, and only if it is clean
git worktree list                   # what is still cut, from any of them
```
