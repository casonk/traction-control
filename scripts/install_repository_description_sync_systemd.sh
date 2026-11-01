#!/usr/bin/env bash
# Render and optionally enable the weekly repository-description sync timer.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_PATH="${REPO_ROOT}/config/clockwork/repository-description-sync-weekly.toml.template"
UNIT_TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CLOCKWORK_REPO_DEFAULT="${REPO_ROOT}/../clockwork"
CLOCKWORK_REPO="${CLOCKWORK_REPO:-${CLOCKWORK_REPO_DEFAULT}}"
RENDER_ONLY=0

usage() {
  cat <<'EOF'
Usage: install_repository_description_sync_systemd.sh [options]

Render the weekly repository-description sync user timer via Clockwork.

Options:
  --render-only          Write unit files only; skip daemon-reload/enable.
  --unit-dir DIR         Override the target unit directory.
  --clockwork-repo PATH  Override the sibling Clockwork checkout.
  --help                 Show this help text.
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
      [[ $# -ge 2 ]] || fail "$1 requires a value"
      UNIT_TARGET_DIR="$2"
      shift 2
      ;;
    --clockwork-repo)
      [[ $# -ge 2 ]] || fail "$1 requires a value"
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
  "${TEMPLATE_PATH}" > "${TMP_MANIFEST}"

"${CLOCKWORK_CMD[@]}" install \
  --manifest "${TMP_MANIFEST}" \
  --target systemd-user \
  --unit-dir "${UNIT_TARGET_DIR}"

if (( RENDER_ONLY == 1 )); then
  exit 0
fi

systemctl --user daemon-reload
systemctl --user enable --now repository-description-sync-weekly.timer
systemctl --user list-timers repository-description-sync-weekly.timer
