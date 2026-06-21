#!/usr/bin/env bash
# install_ci_repair_agentic_repair_systemd.sh — install an on-demand user
# service for the explicit CI repair worker.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_TARGET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PROVIDER_VALUE="${CI_REPAIR_AGENTIC_PROVIDER:-auto}"
MODEL_VALUE="${CI_REPAIR_AGENTIC_MODEL:-}"
LOG_DIR_VALUE="${LOG_DIR:-%h/.local/share/ci-repair-agentic}"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
RENDER_ONLY=0

usage() {
  cat <<EOF
Usage: install_ci_repair_agentic_repair_systemd.sh [options]

Install the explicit on-demand CI repair user service. This does not create or
enable a timer; start the service manually after discovery identifies candidates.

Options:
  --render-only           Write the unit file only; skip daemon-reload/status checks.
  --unit-dir DIR          Override the target unit directory.
  --portfolio-root PATH   Override PORTFOLIO_ROOT in the generated unit.
  --provider NAME         Provider to bake into the unit: auto, codex, claude, or copilot.
  --model MODEL           Model override to bake into the unit for the selected provider.
  --log-dir DIR           Override the run-log directory used by the service.
  --help                  Show this help text.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
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
    --log-dir)
      LOG_DIR_VALUE="$2"
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

mkdir -p "${UNIT_TARGET_DIR}"
UNIT_PATH="${UNIT_TARGET_DIR}/ci-repair-agentic-repair.service"

cat > "${UNIT_PATH}" <<EOF
[Unit]
Description=Explicit CI repair worker for candidate GitHub Actions failures
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=-%h/.config/traction-control/ci-repair-agentic.env
Environment="CI_REPAIR_AGENTIC_PROVIDER=${PROVIDER_VALUE}"
Environment="CI_REPAIR_AGENTIC_MODEL=${MODEL_VALUE}"
Environment="LOG_DIR=${LOG_DIR_VALUE}"
Environment="PATH=%h/.local/npm-global/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PORTFOLIO_ROOT=${PORTFOLIO_ROOT}"
ExecStart=${REPO_ROOT}/scripts/ci_repair_agentic_repair.sh --candidate-file ${LOG_DIR_VALUE}/latest-candidates.tsv --inventory-file ${LOG_DIR_VALUE}/latest-inventory.tsv
StandardOutput=journal
StandardError=journal
EOF

if (( RENDER_ONLY == 1 )); then
  exit 0
fi

systemctl --user daemon-reload
systemctl --user show ci-repair-agentic-repair.service -p FragmentPath
