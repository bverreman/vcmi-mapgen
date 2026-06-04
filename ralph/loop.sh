#!/usr/bin/env bash
# Ralph loop: run Claude Code headless, over and over, on a stateless prompt.
# State lives in files (ralph/backlog.md, ralph/journal.md) and git history.
#
# Usage:  bash ralph/loop.sh [MAX_ITERS]
# Stop:   touch ralph/STOP   (checked at the top of each iteration)
#
# Safety: runs `claude --dangerously-skip-permissions`, so run it in a sandbox /
# disposable environment. Every change is git-committed by the agent and gated by
# ralph/verify.sh, so progress is durable and revertible.
set -uo pipefail
cd "$(dirname "$0")/.."

MAX="${1:-30}"
PROMPT="$(cat ralph/prompt.md)"
mkdir -p ralph
: > ralph/STOP 2>/dev/null && rm -f ralph/STOP   # clear any stale STOP

iter=0
while [ "$iter" -lt "$MAX" ]; do
  iter=$((iter + 1))
  if [ -f ralph/STOP ]; then
    echo "[loop] STOP file present — ending after $((iter - 1)) iterations."
    break
  fi
  echo "================ Ralph iteration $iter / $MAX  $(date -Is) ================" \
    | tee -a ralph/loop.log

  claude -p "$PROMPT" \
    --dangerously-skip-permissions \
    2>&1 | tee -a ralph/loop.log

  echo "---------------- end iteration $iter ----------------" | tee -a ralph/loop.log
  sleep 2
done
echo "[loop] done ($iter iterations)."
