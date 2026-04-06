#!/usr/bin/env bash
# install_archility_daily_systemd.sh — render and optionally enable the daily
# archility user-level timer via the shared clockwork scheduler utility.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
TEMPLATE_PATH="${REPO_ROOT}/config/clockwork/archility-daily.toml.template"
UNIT_TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CLOCKWORK_REPO_DEFAULT="${REPO_ROOT}/../clockwork"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
ARCHILITY_CMD_VALUE="${ARCHILITY_CMD:-archility}"
CLOCKWORK_REPO="${CLOCKWORK_REPO:-${CLOCKWORK_REPO_DEFAULT}}"
RENDER_ONLY=0

usage() {
  cat <<EOF
Usage: install_archility_daily_systemd.sh [options]

Render the archility daily user-level systemd files via clockwork and enable
the timer by default.

Options:
  --render-only           Write the unit files only; skip daemon-reload/enable.
  --unit-dir DIR          Override the target unit directory.
  --portfolio-root PATH   Override PORTFOLIO_ROOT in the generated unit.
  --archility-cmd CMD     Override ARCHILITY_CMD in the generated unit.
  --clockwork-repo PATH   Override the sibling clockwork repo path fallback.
  --help                  Show this help text.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --render-only)
      RENDER_ONLY=1
      shift
      ;;
    --unit-dir)
      UNIT_TARGET_DIR="$2"
      shift 2
      ;;
    --portfolio-root)
      PORTFOLIO_ROOT="$2"
      shift 2
      ;;
    --archility-cmd)
      ARCHILITY_CMD_VALUE="$2"
      shift 2
      ;;
    --clockwork-repo)
      CLOCKWORK_REPO="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

command -v python3 >/dev/null 2>&1 || fail "python3 not found"
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

sed \
  -e "s|__REPO_ROOT__|$(escape_sed_replacement "${REPO_ROOT}")|g" \
  -e "s|__PORTFOLIO_ROOT__|$(escape_sed_replacement "${PORTFOLIO_ROOT}")|g" \
  -e "s|__ARCHILITY_CMD__|$(escape_sed_replacement "${ARCHILITY_CMD_VALUE}")|g" \
  "${TEMPLATE_PATH}" > "${TMP_MANIFEST}"

"${CLOCKWORK_CMD[@]}" install \
  --manifest "${TMP_MANIFEST}" \
  --target systemd-user \
  --unit-dir "${UNIT_TARGET_DIR}"

if (( RENDER_ONLY == 1 )); then
  exit 0
fi

systemctl --user daemon-reload
systemctl --user enable --now archility-daily.timer
systemctl --user list-timers archility-daily.timer
