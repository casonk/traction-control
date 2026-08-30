#!/usr/bin/env bash
# Run the activation fixture in the reusable, full-system Vagrant Ubuntu VM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${REPO_ROOT}/../.." && pwd)"
VAGRANT_DIR="${TRACTION_CONTROL_VAGRANT_DIR:-${WORKSPACE_ROOT}/util-repos/install-harness/vagrant}"
MACHINE="${TRACTION_CONTROL_VAGRANT_MACHINE:-ubuntu2404}"
GUEST_SCRIPT="/portfolio/util-repos/traction-control/tests/vagrant/test_live_systemd_activation_guest.sh"

[[ -d "${VAGRANT_DIR}" && -f "${VAGRANT_DIR}/Vagrantfile" ]] \
  || { printf 'error: Vagrant harness not found: %s\n' "${VAGRANT_DIR}" >&2; exit 1; }
[[ "${MACHINE}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || { printf 'error: unsafe Vagrant machine name: %s\n' "${MACHINE}" >&2; exit 2; }

(
  cd "${VAGRANT_DIR}"
  vagrant up "${MACHINE}"
  vagrant ssh "${MACHINE}" -c "bash ${GUEST_SCRIPT}"
)
