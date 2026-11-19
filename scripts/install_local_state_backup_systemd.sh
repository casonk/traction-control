#!/usr/bin/env bash
# install_local_state_backup_systemd.sh — render and enable the two daily local-state backup timers via clockwork.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-$(cd "${REPO_ROOT}/../.." && pwd)}"
CLOCKWORK_REPO="${CLOCKWORK_REPO:-${REPO_ROOT}/../clockwork}"
TEMPLATE_PATH="${REPO_ROOT}/config/clockwork/local-state-backup.toml.template"
UNIT_TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
RENDER_ONLY=0

usage() {
  cat <<USAGE
Usage: install_local_state_backup_systemd.sh [--render-only] [--unit-dir DIR] [--portfolio-root PATH] [--clockwork-repo PATH]

Renders local-state-backup.timer, state-dataset-backup.timer and state-restic-backup.timer and enables them. Run \`python3 scripts/state_dataset_backup.py plan\`
first: the dataset job needs config/state-datasets.local.toml.
USAGE
}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
escape() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --render-only) RENDER_ONLY=1; shift ;;
    --unit-dir) UNIT_TARGET_DIR="$2"; shift 2 ;;
    --portfolio-root) PORTFOLIO_ROOT="$2"; shift 2 ;;
    --clockwork-repo) CLOCKWORK_REPO="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

command -v python3 >/dev/null 2>&1 || fail "python3 not found"
command -v age >/dev/null 2>&1 || fail "age not found (the dataset backup encrypts with it)"
command -v zstd >/dev/null 2>&1 || fail "zstd not found"
command -v restic >/dev/null 2>&1 || fail "restic not found (the restic job needs it)"
[[ -f "${TEMPLATE_PATH}" ]] || fail "missing template: ${TEMPLATE_PATH}"

if command -v clockwork >/dev/null 2>&1; then
  CLOCKWORK_CMD=(clockwork)
else
  [[ -d "${CLOCKWORK_REPO}/src/clockwork" ]] || fail "clockwork not found at ${CLOCKWORK_REPO}"
  export PYTHONPATH="${CLOCKWORK_REPO}/src${PYTHONPATH:+:${PYTHONPATH}}"
  CLOCKWORK_CMD=(python3 -m clockwork)
fi

TMP_MANIFEST="$(mktemp)"
trap 'rm -f "${TMP_MANIFEST}"' EXIT
sed -e "s|__REPO_ROOT__|$(escape "${REPO_ROOT}")|g" -e "s|__PORTFOLIO_ROOT__|$(escape "${PORTFOLIO_ROOT}")|g" \
  "${TEMPLATE_PATH}" > "${TMP_MANIFEST}"

"${CLOCKWORK_CMD[@]}" install --manifest "${TMP_MANIFEST}" --target systemd-user --unit-dir "${UNIT_TARGET_DIR}"

if (( RENDER_ONLY == 1 )); then
  exit 0
fi
systemctl --user daemon-reload
systemctl --user enable --now local-state-backup.timer state-dataset-backup.timer state-restic-backup.timer
systemctl --user list-timers local-state-backup.timer state-dataset-backup.timer state-restic-backup.timer
