#!/usr/bin/env bash
# Commit and push the agent's spec/code edits to the result branch so they
# survive the ephemeral container. Idempotent: a no-op commit is skipped.
#
# Args:
#   $1  repo dir       (e.g. /workspace/assembler)
#   $2  result branch  (e.g. hrnet-research/auto)
#
# Outputs JSON: {"publish_result": {"pushed": true|false, "branch": "...", "status": "ok"}}
set -euo pipefail

REPO_DIR="${1:?repo dir required}"
RESULT_BRANCH="${2:-hrnet-research/auto}"

cd "$REPO_DIR"

git config user.email "hrnet-researcher@local" >/dev/null 2>&1 || true
git config user.name  "HRNet Researcher"        >/dev/null 2>&1 || true

# Work on the result branch (create or switch).
git checkout -B "$RESULT_BRANCH" >&2

git add -A

if git diff --cached --quiet; then
  echo "[publish] no changes to commit" >&2
  echo "{\"publish_result\": {\"pushed\": false, \"branch\": \"$RESULT_BRANCH\", \"status\": \"ok\"}}"
  exit 0
fi

git commit --quiet -m "hrnet-research: automated gate update" >&2

if git push --quiet --force-with-lease origin "$RESULT_BRANCH" >&2; then
  echo "{\"publish_result\": {\"pushed\": true, \"branch\": \"$RESULT_BRANCH\", \"status\": \"ok\"}}"
else
  # No write credential / no remote: keep edits local; artifacts still capture them.
  echo "[publish] push failed — edits remain on local branch $RESULT_BRANCH only" >&2
  echo "{\"publish_result\": {\"pushed\": false, \"branch\": \"$RESULT_BRANCH\", \"status\": \"push_failed\"}}"
fi
