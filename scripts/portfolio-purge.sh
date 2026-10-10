#!/usr/bin/env bash
# portfolio-purge.sh — weekly removal of stale local git state across the portfolio
#
# Runs scripts/portfolio_purge.py with --apply against PORTFOLIO_ROOT. By
# default it only touches local state that is provably finished:
#   - worktrees that are clean, unlocked, older than the age guard, and on a
#     branch already in origin/main
#   - local branches with a merged PR, or whose merge into origin/main is a no-op
#
# Two wider scopes are opt-in, because each reaches beyond this checkout:
#   PORTFOLIO_PURGE_REMOTE_BRANCHES=1   also delete merged branches on origin
#   PORTFOLIO_PURGE_IMAGES=1            also prune dangling podman layers
#
# Other settings:
#   PORTFOLIO_PURGE_MIN_AGE_DAYS        worktree age guard (default: 3)
#   PORTFOLIO_PURGE_DRY_RUN=1           report only; delete nothing
#
# Exit code 0 = ran cleanly (including "nothing to do"); 1 = a deletion failed;
# 2 = setup error. Logs are written to LOG_DIR
# (default: ~/.local/share/portfolio-purge/).
# Run manually to verify: PORTFOLIO_PURGE_DRY_RUN=1 bash /path/to/portfolio-purge.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/portfolio-purge}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"

if [[ ! -d "${PORTFOLIO_ROOT}" ]]; then
  printf 'error: PORTFOLIO_ROOT is not a directory: %s\n' "${PORTFOLIO_ROOT}" >&2
  exit 2
fi
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  printf 'error: %s not found on PATH\n' "${PYTHON_BIN}" >&2
  exit 2
fi

mkdir -p "${LOG_DIR}"

ARGS=(--portfolio-root "${PORTFOLIO_ROOT}" --verbose
      --min-age-days "${PORTFOLIO_PURGE_MIN_AGE_DAYS:-3}")
[[ "${PORTFOLIO_PURGE_DRY_RUN:-0}" == "1" ]] || ARGS+=(--apply)
[[ "${PORTFOLIO_PURGE_REMOTE_BRANCHES:-0}" == "1" ]] && ARGS+=(--remote-branches)
[[ "${PORTFOLIO_PURGE_IMAGES:-0}" == "1" ]] && ARGS+=(--images)
# Without gh the purge still works, but finds only what git alone can prove.
command -v gh >/dev/null 2>&1 || ARGS+=(--no-gh)

{
  printf '[%s] portfolio purge: %s\n' "$(date '+%H:%M:%S')" "${PORTFOLIO_ROOT}"
  printf '[%s] arguments: %s\n' "$(date '+%H:%M:%S')" "${ARGS[*]}"
} | tee -a "${LOG_FILE}"

status=0
"${PYTHON_BIN}" "${SCRIPT_DIR}/portfolio_purge.py" "${ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}" || status=$?

ln -sfn "${LOG_FILE}" "${LATEST_LINK}"
exit "${status}"
