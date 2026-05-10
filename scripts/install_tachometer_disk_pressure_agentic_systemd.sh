#!/usr/bin/env bash
# install_tachometer_disk_pressure_agentic_systemd.sh — render and optionally
# enable the tachometer disk-pressure remediation timer via clockwork.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
TEMPLATE_PATH="${REPO_ROOT}/config/clockwork/tachometer-disk-pressure-agentic.toml.template"
UNIT_TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CLOCKWORK_REPO_DEFAULT="${REPO_ROOT}/../clockwork"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
CLOCKWORK_REPO="${CLOCKWORK_REPO:-${CLOCKWORK_REPO_DEFAULT}}"
PROVIDER_VALUE="${TACHOMETER_DISK_PRESSURE_AGENTIC_PROVIDER:-auto}"
MODEL_VALUE="${TACHOMETER_DISK_PRESSURE_AGENTIC_MODEL:-}"
RENDER_ONLY=0

usage() {
  cat <<EOF
Usage: install_tachometer_disk_pressure_agentic_systemd.sh [options]

Render the tachometer disk-pressure user-level systemd timer via clockwork and
enable it by default.

Options:
  --render-only           Write the unit files only; skip daemon-reload/enable.
  --unit-dir DIR          Override the target unit directory.
  --portfolio-root PATH   Override PORTFOLIO_ROOT in the generated unit.
  --provider NAME         Provider to bake into the unit: auto, codex, claude, or copilot.
  --model MODEL           Model override to bake into the unit for the selected provider.
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
    --provider)
      PROVIDER_VALUE="$2"
      shift 2
      ;;
    --model)
      MODEL_VALUE="$2"
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

case "${PROVIDER_VALUE}" in
  auto|codex|claude|copilot)
    ;;
  *)
    fail "unsupported provider: ${PROVIDER_VALUE}"
    ;;
esac

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
  -e "s|__PROVIDER__|$(escape_sed_replacement "${PROVIDER_VALUE}")|g" \
  -e "s|__MODEL__|$(escape_sed_replacement "${MODEL_VALUE}")|g" \
  "${TEMPLATE_PATH}" > "${TMP_MANIFEST}"

"${CLOCKWORK_CMD[@]}" install \
  --manifest "${TMP_MANIFEST}" \
  --target systemd-user \
  --unit-dir "${UNIT_TARGET_DIR}"

if (( RENDER_ONLY == 1 )); then
  exit 0
fi

systemctl --user daemon-reload
systemctl --user enable --now tachometer-disk-pressure-agentic.timer
systemctl --user list-timers tachometer-disk-pressure-agentic.timer
