#!/usr/bin/env bash
# Install the Azure runner power job (as your user). Renders the template with this checkout's path
# into the ignored config/clockwork/azure-runner-power.toml and installs it through Clockwork.
#
# Usage: bash scripts/install_azure_runner_power.sh [--render-only]
#   CLOCKWORK_DIR  Clockwork checkout (default: ../clockwork)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_ROOT}/config/clockwork/azure-runner-power.toml.template"
MANIFEST="${REPO_ROOT}/config/clockwork/azure-runner-power.toml"
CLOCKWORK_DIR="${CLOCKWORK_DIR:-${REPO_ROOT}/../clockwork}"

sed "s#__REPO_ROOT__#${REPO_ROOT}#g" "${TEMPLATE}" > "${MANIFEST}"
printf 'rendered %s\n' "${MANIFEST}"
[[ "${1:-}" == "--render-only" ]] && exit 0

[[ -d "${CLOCKWORK_DIR}" ]] || { printf 'error: Clockwork not found at %s (set CLOCKWORK_DIR)\n' "${CLOCKWORK_DIR}" >&2; exit 1; }
(cd "${CLOCKWORK_DIR}" && python3 -m clockwork install --manifest "${MANIFEST}" --target systemd-user)
systemctl --user daemon-reload
systemctl --user enable --now azure-runner-power.timer
printf 'installed azure-runner-power.timer\n'
