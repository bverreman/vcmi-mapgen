# Ralph loop for the VCMI map generator

A "Ralph loop" runs an AI coding agent in a `while` loop on a **stateless** prompt:
each iteration is a fresh Claude Code session that reads the backlog, does ONE task,
verifies it against a fitness gate, commits, and exits. State lives in files and git,
not in the model's context — so the loop is durable, resumable, and revertible.

## Pieces
- `prompt.md`  — the persistent instruction handed to Claude each iteration.
- `backlog.md` — prioritised task list; the agent works the top `[ ]` item.
- `verify.sh`  — the fitness gate (generation runs, object-distance ≤ 3, editor loads).
- `loop.sh`    — the runner (`claude -p` in a bounded `while` loop).
- `journal.md` — append-only progress log written by the agent.
- `loop.log`   — full transcript of every iteration (written by `loop.sh`).
- `STOP`       — create this file to end the loop after the current iteration.

## Run it
```bash
cd /mnt/data/workspace/vcmi-mapgen

# one manual dry-run of the gate (fast: skips the slow editor load-test)
RALPH_SKIP_LOADTEST=1 bash ralph/verify.sh

# run the loop for up to 20 iterations
bash ralph/loop.sh 20

# stop it gracefully
touch ralph/STOP
```

## How it stays safe
- `loop.sh` calls `claude --dangerously-skip-permissions`, so **run it in a sandbox
  or disposable VM/container**. It can edit files and run commands unattended.
- Every iteration is git-committed by the agent and gated by `verify.sh`; nothing is
  committed unless the gate passes, so you can always `git log` / `git revert`.
- The agent may only write generated maps to `out/` and one temp file the load-test
  uses — never into your real Maps folder.

## Tuning
- Speed vs rigour: `verify.sh` runs the ~90s headless editor load-test each time.
  Export `RALPH_SKIP_LOADTEST=1` to skip it during fast iterations (the loop will
  still check generation + object-distance), but run it unskipped before trusting a
  batch of commits.
- The fitness bar is `object-distance ≤ 3 tiles` plus "loads with no fatal errors".
  Tighten it in `verify.sh` as quality improves (e.g. add a max total-distance, or a
  traversability assertion) to keep ratcheting the generator toward real maps.
