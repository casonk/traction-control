#!/usr/bin/env bash
# Run the activation fixture in the reusable, full-system Vagrant Ubuntu VM.
#
# The Vagrant harness lives outside this repository, so its location is supplied
# at runtime via TRACTION_CONTROL_VAGRANT_DIR rather than hardcoded here. When it
# is unset the live-VM test cannot run (and it never runs in CI), so it skips.
set -euo pipefail

VAGRANT_DIR="${TRACTION_CONTROL_VAGRANT_DIR:-}"
MACHINE="${TRACTION_CONTROL_VAGRANT_MACHINE:-ubuntu2404}"
GUEST_SCRIPT="/portfolio/util-repos/traction-control/tests/vagrant/test_live_systemd_activation_guest.sh"

if [[ -z "${VAGRANT_DIR}" ]]; then
  printf 'skip: set TRACTION_CONTROL_VAGRANT_DIR to the Vagrant harness directory\n' >&2
  exit 0
fi

[[ -d "${VAGRANT_DIR}" && -f "${VAGRANT_DIR}/Vagrantfile" ]] \
  || { printf 'error: Vagrant harness not found: %s\n' "${VAGRANT_DIR}" >&2; exit 1; }
[[ "${MACHINE}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || { printf 'error: unsafe Vagrant machine name: %s\n' "${MACHINE}" >&2; exit 2; }

(
  cd "${VAGRANT_DIR}"
  vagrant up "${MACHINE}"
  vagrant ssh "${MACHINE}" -c "bash ${GUEST_SCRIPT}"
)
