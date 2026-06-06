#!/usr/bin/env bash
# Clone the assembler repo into /workspace so the agent works against its own
# clean checkout — never a host working tree.
#
# Args:
#   $1  repo url    (e.g. git@github.com:<org>/assembler.git)
#   $2  repo branch (default: main)
#
# Outputs JSON: {"setup_result": {"repo_dir": "...", "status": "ok"}}
set -euo pipefail

# Native in-place mode: when AGENT_REPO_DIR is set, operate directly on that
# existing checkout instead of cloning into /workspace. Used by the generated
# launcher's `*-native` target, which runs the controller on the host so the
# agent edits and commits straight into the real repo (no Docker, no clone).
# HRNET_REPO_DIR is accepted as a backward-compat alias for AGENT_REPO_DIR.
if [ -n "${AGENT_REPO_DIR:-}" ] || [ -n "${HRNET_REPO_DIR:-}" ]; then
  REPO_DIR="${AGENT_REPO_DIR:-${HRNET_REPO_DIR}}"
  git config --global --add safe.directory '*' 2>/dev/null || true
  echo "[setup] in-place mode: using existing repo at $REPO_DIR (no clone)" >&2
  echo "{\"setup_result\": {\"repo_dir\": \"$REPO_DIR\", \"status\": \"ok\"}}"
  exit 0
fi

# Env wins over args so a compose override can redirect the clone source (e.g. to
# a read-only bind mount of a local repo) without editing the committed workflow.
REPO_URL="${REPO_URL:-${1:?repo url required}}"
REPO_BRANCH="${REPO_BRANCH:-${2:-main}}"

# Accept GitHub's host key non-interactively when cloning over SSH (no-op for a
# local path source).
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o StrictHostKeyChecking=accept-new}"

# A local bind-mounted source repo is owned by the host user, not the container's
# `nobody`; git refuses such repos by default ("dubious ownership"). Trust them —
# this container is disposable and isolated.
git config --global --add safe.directory '*'

WORKSPACE=/workspace
REPO_DIR="$WORKSPACE/assembler"

mkdir -p "$WORKSPACE"

if [ -d "$REPO_DIR/.git" ]; then
  echo "[$REPO_DIR] already cloned — fetching and checking out $REPO_BRANCH" >&2
  git -C "$REPO_DIR" fetch --quiet origin
  git -C "$REPO_DIR" checkout --quiet "$REPO_BRANCH"
  git -C "$REPO_DIR" reset --quiet --hard "origin/$REPO_BRANCH"
else
  echo "[$REPO_DIR] cloning $REPO_URL @ $REPO_BRANCH" >&2
  git clone --quiet --branch "$REPO_BRANCH" --single-branch "$REPO_URL" "$REPO_DIR"
fi

# Install Python deps so the agent can run pytest/ruff inside the clone.
( cd "$REPO_DIR" && uv sync --no-sources >&2 ) || \
  echo "[setup] warning: 'uv sync --no-sources' failed; agent must resolve deps" >&2

echo "{\"setup_result\": {\"repo_dir\": \"$REPO_DIR\", \"status\": \"ok\"}}"
