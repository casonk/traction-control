#!/usr/bin/env bash
# Install the portfolio main-branch guard as this user's global git hooks.
#
# Copies scripts/git-hooks/ into a stable per-user directory and points
# `git config --global core.hooksPath` at it, so every checkout on the machine
# refuses direct commits and pushes to main/master unless explicitly allowed.
# Repo-local .git/hooks/* still run after the guard passes.
#
# Usage: install_main_branch_guard.sh [--dest DIR] [--force] [--uninstall]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/git-hooks"
DEST="${XDG_CONFIG_HOME:-${HOME}/.config}/git/main-branch-guard"
FORCE=0
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h | --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

current="$(git config --global --get core.hooksPath || true)"

if [ "${UNINSTALL}" -eq 1 ]; then
  if [ "${current}" = "${DEST}" ]; then
    git config --global --unset core.hooksPath
    echo "removed global core.hooksPath (${DEST})"
  else
    echo "global core.hooksPath is not the guard (${current:-unset}); left unchanged"
  fi
  exit 0
fi

if [ -n "${current}" ] && [ "${current}" != "${DEST}" ] && [ "${FORCE}" -ne 1 ]; then
  echo "global core.hooksPath is already ${current}; rerun with --force to replace it" >&2
  exit 1
fi

mkdir -p "${DEST}"
for name in main-branch-guard.sh pre-commit pre-push; do
  install -m 0755 "${SOURCE_DIR}/${name}" "${DEST}/${name}"
done
git config --global core.hooksPath "${DEST}"

echo "installed main-branch guard hooks in ${DEST}"
echo "note: 'pre-commit install' refuses to run while core.hooksPath is set;"
echo "      keep running 'pre-commit run --all-files' before each push."
