#!/usr/bin/env bash
# Host-safe stub coverage for install_podman_runtime.sh.
#
# The installer runs with a PATH containing only the commands created below.
# Real Podman, Homebrew, sudo, and Linux package managers are never reachable.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALLER="${REPO_ROOT}/scripts/install_podman_runtime.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/podman-runtime-tests.XXXXXX")"
STUB_TEMPLATE_DIR="${TEST_ROOT}/stub-templates"
STUB_LIB="${TEST_ROOT}/stub-lib.sh"
REAL_CP="$(command -v cp)"

PASS_COUNT=0
FAIL_COUNT=0
COMMAND_OUTPUT=""
COMMAND_STATUS=0
CASE_ROOT=""
ACTIVE_BIN=""
COMMAND_LOG=""
MACHINE_STATE_FILE=""
MACHINE_ROOTFUL_FILE=""

cleanup() {
  case "${TEST_ROOT}" in
    "${TMPDIR:-/tmp}"/podman-runtime-tests.*) rm -rf "${TEST_ROOT}" ;;
  esac
}
trap cleanup EXIT HUP INT TERM

pass() {
  PASS_COUNT=$(( PASS_COUNT + 1 ))
  printf 'ok %s - %s\n' "${PASS_COUNT}" "$1"
}

fail_test() {
  FAIL_COUNT=$(( FAIL_COUNT + 1 ))
  printf 'not ok - %s\n' "$1" >&2
  if [[ -n "${2:-}" ]]; then
    printf '  %s\n' "$2" >&2
  fi
}

assert_status() {
  local expected="$1"
  local label="$2"
  if [[ "${COMMAND_STATUS}" -eq "${expected}" ]]; then
    pass "${label}"
  else
    fail_test "${label}" "expected status ${expected}, got ${COMMAND_STATUS}: ${COMMAND_OUTPUT}"
  fi
}

assert_failure() {
  local label="$1"
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "${label}"
  else
    fail_test "${label}" "command unexpectedly succeeded: ${COMMAND_OUTPUT}"
  fi
}

assert_output_contains() {
  local expected="$1"
  local label="$2"
  case "${COMMAND_OUTPUT}" in
    *"${expected}"*) pass "${label}" ;;
    *) fail_test "${label}" "missing output: ${expected}" ;;
  esac
}

assert_logged_line() {
  local expected="$1"
  local label="$2"
  if grep -Fqx -- "${expected}" "${COMMAND_LOG}"; then
    pass "${label}"
  else
    fail_test "${label}" "missing command: ${expected}"
  fi
}

assert_no_logged_line() {
  local unexpected="$1"
  local label="$2"
  if ! grep -Fqx -- "${unexpected}" "${COMMAND_LOG}"; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected command: ${unexpected}"
  fi
}

assert_no_logged_command() {
  local command_name="$1"
  local label="$2"
  if ! grep -q "^${command_name}\([[:space:]]\|$\)" "${COMMAND_LOG}" 2>/dev/null; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected ${command_name} invocation"
  fi
}

assert_no_podman_machine_action() {
  local action="$1"
  local label="$2"
  if ! grep -q "^podman[[:space:]]machine[[:space:]]${action}\([[:space:]]\|$\)" "${COMMAND_LOG}" 2>/dev/null; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected podman machine ${action} invocation"
  fi
}

assert_machine_state() {
  local expected_state="$1"
  local expected_rootful="$2"
  local label="$3"
  local actual_state=""
  local actual_rootful=""
  [[ -f "${MACHINE_STATE_FILE}" ]] && actual_state="$(<"${MACHINE_STATE_FILE}")"
  [[ -f "${MACHINE_ROOTFUL_FILE}" ]] && actual_rootful="$(<"${MACHINE_ROOTFUL_FILE}")"
  if [[ "${actual_state}" == "${expected_state}" && "${actual_rootful}" == "${expected_rootful}" ]]; then
    pass "${label}"
  else
    fail_test "${label}" "expected ${expected_state}/${expected_rootful}, got ${actual_state}/${actual_rootful}"
  fi
}

assert_no_unsafe_commands() {
  local label="$1"
  if grep -E \
    '^(curl|wget)([[:space:]]|$)|^podman[[:space:]]machine[[:space:]](rm|reset|set)([[:space:]]|$)|--rootful=true|podman-desktop|[[:space:]](upgrade|dist-upgrade)([[:space:]]|$)' \
    "${COMMAND_LOG}" >/dev/null 2>&1; then
    fail_test "${label}" "unsafe command recorded: $(sed -n '1,20p' "${COMMAND_LOG}")"
  else
    pass "${label}"
  fi
}

write_stubs() {
  mkdir -p "${STUB_TEMPLATE_DIR}"

  cat > "${STUB_LIB}" <<'STUB'
log_stub_command() {
  local command_name="$1"
  shift
  printf '%s' "${command_name}" >> "${TEST_COMMAND_LOG:?}"
  for command_arg in "$@"; do
    printf '\t%s' "${command_arg}" >> "${TEST_COMMAND_LOG}"
  done
  printf '\n' >> "${TEST_COMMAND_LOG}"
}

activate_podman_stub() {
  "${TEST_REAL_CP:?}" -p "${TEST_PODMAN_TEMPLATE:?}" "${TEST_ACTIVE_BIN:?}/podman"
}

read_machine_state() {
  if [[ -f "${TEST_MACHINE_STATE_FILE:?}" ]]; then
    printf '%s\n' "$(<"${TEST_MACHINE_STATE_FILE}")"
  else
    printf '%s\n' absent
  fi
}

read_machine_rootful() {
  if [[ -f "${TEST_MACHINE_ROOTFUL_FILE:?}" ]]; then
    printf '%s\n' "$(<"${TEST_MACHINE_ROOTFUL_FILE}")"
  else
    printf '%s\n' false
  fi
}
STUB

  cat > "${STUB_TEMPLATE_DIR}/uname" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command uname "$@"
[[ "$#" -eq 1 ]] || exit 64
case "$1" in
  -s) printf '%s\n' "${TEST_OS:?}" ;;
  -m) printf '%s\n' "${TEST_ARCH:?}" ;;
  *) exit 64 ;;
esac
STUB

  cat > "${STUB_TEMPLATE_DIR}/id" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command id "$@"
[[ "$#" -eq 1 && "$1" == "-u" ]] || exit 64
printf '%s\n' "${TEST_UID:?}"
STUB

  cat > "${STUB_TEMPLATE_DIR}/cat" <<'STUB'
#!/bin/bash
set -u
[[ "$#" -eq 0 ]] || exit 64
while IFS= read -r input_line; do
  printf '%s\n' "${input_line}"
done
STUB

  cat > "${STUB_TEMPLATE_DIR}/sw_vers" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command sw_vers "$@"
[[ "$#" -eq 1 && "$1" == "-productVersion" ]] || exit 64
printf '%s\n' "${TEST_MACOS_VERSION:?}"
STUB

  cat > "${STUB_TEMPLATE_DIR}/brew" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command brew "$@"
[[ "$#" -eq 2 && "$1" == "install" && "$2" == "podman" ]] || exit 64
[[ "${TEST_BREW_FAIL:-0}" == "0" ]] || exit 42
activate_podman_stub
STUB

  cat > "${STUB_TEMPLATE_DIR}/sudo" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command sudo "$@"
case "${1:-}" in
  apt-get|dnf) ;;
  *) exit 64 ;;
esac
"$@"
STUB

  cat > "${STUB_TEMPLATE_DIR}/apt-get" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command apt-get "$@"
if [[ "$#" -eq 1 && "$1" == "update" ]]; then
  [[ "${TEST_PACKAGE_FAIL:-0}" == "0" ]] || exit 43
  exit 0
fi
if [[ "$#" -eq 3 && "$1" == "install" && "$2" == "-y" && "$3" == "podman" ]]; then
  [[ "${TEST_PACKAGE_FAIL:-0}" == "0" ]] || exit 43
  activate_podman_stub
  exit 0
fi
exit 64
STUB

  cat > "${STUB_TEMPLATE_DIR}/dnf" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command dnf "$@"
[[ "$#" -eq 3 && "$1" == "install" && "$2" == "-y" && "$3" == "podman" ]] || exit 64
[[ "${TEST_PACKAGE_FAIL:-0}" == "0" ]] || exit 43
activate_podman_stub
STUB

  cat > "${STUB_TEMPLATE_DIR}/podman" <<'STUB'
#!/bin/bash
set -u
. "${TEST_STUB_LIB:?}"
log_stub_command podman "$@"

machine_state="$(read_machine_state)"
machine_rootful="$(read_machine_rootful)"

if [[ "$#" -eq 4 && "$1" == "machine" && "$2" == "list" \
  && "$3" == "--format" && "$4" == "{{.Name}}" ]]; then
  if [[ "${machine_state}" != "absent" ]]; then
    printf '%s\n' "${TEST_MACHINE_NAME:?}"
  fi
  exit 0
fi

if [[ "$#" -eq 5 && "$1" == "machine" && "$2" == "inspect" \
  && "$3" == "--format" && "$5" == "${TEST_MACHINE_NAME:?}" ]]; then
  [[ "${machine_state}" != "absent" ]] || exit 125
  case "$4" in
    '{{.State}}') printf '%s\n' "${machine_state}" ;;
    '{{.Rootful}}') printf '%s\n' "${machine_rootful}" ;;
    *) exit 64 ;;
  esac
  exit 0
fi

if [[ "$#" -eq 11 && "$1" == "machine" && "$2" == "init" \
  && "$3" == "--rootful=false" && "$4" == "--update-connection=false" \
  && "$5" == "--cpus" && "$6" == "${TEST_EXPECT_CPUS:?}" \
  && "$7" == "--memory" && "$8" == "${TEST_EXPECT_MEMORY:?}" \
  && "$9" == "--disk-size" && "${10}" == "${TEST_EXPECT_DISK:?}" \
  && "${11}" == "${TEST_MACHINE_NAME}" ]]; then
  [[ "${machine_state}" == "absent" ]] || exit 65
  printf '%s\n' stopped > "${TEST_MACHINE_STATE_FILE}"
  printf '%s\n' false > "${TEST_MACHINE_ROOTFUL_FILE}"
  exit 0
fi

if [[ "$#" -eq 4 && "$1" == "machine" && "$2" == "start" \
  && "$3" == "--update-connection=false" && "$4" == "${TEST_MACHINE_NAME:?}" ]]; then
  [[ "${machine_state}" == "stopped" ]] || exit 66
  printf '%s\n' running > "${TEST_MACHINE_STATE_FILE}"
  exit 0
fi

if [[ "$#" -eq 3 && "$1" == "--connection" \
  && "$2" == "${TEST_MACHINE_NAME:?}" && "$3" == "info" ]]; then
  [[ "${TEST_OS:?}" == "Darwin" && "${machine_state}" == "running" \
    && "${machine_rootful}" == "false" ]] || exit 67
  [[ "${TEST_PODMAN_INFO_FAIL:-0}" == "0" ]] || exit 68
  printf '%s\n' 'host.security.rootless=true'
  exit 0
fi

if [[ "$#" -eq 1 && "$1" == "info" ]]; then
  [[ "${TEST_OS:?}" == "Linux" ]] || exit 67
  [[ "${TEST_PODMAN_INFO_FAIL:-0}" == "0" ]] || exit 68
  printf '%s\n' 'host.security.rootless=true'
  exit 0
fi

if [[ "$#" -eq 6 && "$1" == "--connection" \
  && "$2" == "${TEST_MACHINE_NAME:?}" && "$3" == "run" \
  && "$4" == "--rm" && "$5" == "--pull=missing" \
  && "$6" == "quay.io/podman/hello:latest" ]]; then
  [[ "${TEST_OS:?}" == "Darwin" && "${machine_state}" == "running" ]] || exit 67
  exit 0
fi

if [[ "$#" -eq 4 && "$1" == "run" && "$2" == "--rm" \
  && "$3" == "--pull=missing" && "$4" == "quay.io/podman/hello:latest" ]]; then
  [[ "${TEST_OS:?}" == "Linux" ]] || exit 67
  exit 0
fi

exit 64
STUB

  chmod 0755 "${STUB_TEMPLATE_DIR}"/*
}

prepare_case() {
  local case_name="$1"
  CASE_ROOT="${TEST_ROOT}/${case_name}"
  ACTIVE_BIN="${CASE_ROOT}/bin"
  COMMAND_LOG="${CASE_ROOT}/commands.log"
  MACHINE_STATE_FILE="${CASE_ROOT}/machine-state"
  MACHINE_ROOTFUL_FILE="${CASE_ROOT}/machine-rootful"
  mkdir -p "${ACTIVE_BIN}" "${CASE_ROOT}/home"
  : > "${COMMAND_LOG}"
  printf '%s\n' absent > "${MACHINE_STATE_FILE}"
  printf '%s\n' false > "${MACHINE_ROOTFUL_FILE}"
  "${REAL_CP}" -p "${STUB_TEMPLATE_DIR}/uname" "${ACTIVE_BIN}/uname"
  "${REAL_CP}" -p "${STUB_TEMPLATE_DIR}/id" "${ACTIVE_BIN}/id"
  "${REAL_CP}" -p "${STUB_TEMPLATE_DIR}/cat" "${ACTIVE_BIN}/cat"
  "${REAL_CP}" -p "${STUB_TEMPLATE_DIR}/sw_vers" "${ACTIVE_BIN}/sw_vers"
}

enable_stub() {
  local command_name="$1"
  "${REAL_CP}" -p "${STUB_TEMPLATE_DIR}/${command_name}" "${ACTIVE_BIN}/${command_name}"
}

install_podman_stub() {
  enable_stub podman
}

set_machine_state() {
  printf '%s\n' "$1" > "${MACHINE_STATE_FILE}"
  printf '%s\n' "$2" > "${MACHINE_ROOTFUL_FILE}"
}

run_installer() {
  local host_os="$1"
  local host_uid="$2"
  shift 2
  COMMAND_OUTPUT="$(
    PATH="${ACTIVE_BIN}" \
    HOME="${CASE_ROOT}/home" \
    TEST_OS="${host_os}" \
    TEST_ARCH="${TEST_ARCH:-arm64}" \
    TEST_MACOS_VERSION="${TEST_MACOS_VERSION:-14.5}" \
    TEST_UID="${host_uid}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    TEST_STUB_LIB="${STUB_LIB}" \
    TEST_REAL_CP="${REAL_CP}" \
    TEST_PODMAN_TEMPLATE="${STUB_TEMPLATE_DIR}/podman" \
    TEST_ACTIVE_BIN="${ACTIVE_BIN}" \
    TEST_MACHINE_STATE_FILE="${MACHINE_STATE_FILE}" \
    TEST_MACHINE_ROOTFUL_FILE="${MACHINE_ROOTFUL_FILE}" \
    TEST_MACHINE_NAME="${TEST_MACHINE_NAME:-podman-machine-default}" \
    TEST_EXPECT_CPUS="${TEST_EXPECT_CPUS:-2}" \
    TEST_EXPECT_MEMORY="${TEST_EXPECT_MEMORY:-4096}" \
    TEST_EXPECT_DISK="${TEST_EXPECT_DISK:-30}" \
    TEST_BREW_FAIL="${TEST_BREW_FAIL:-0}" \
    TEST_PACKAGE_FAIL="${TEST_PACKAGE_FAIL:-0}" \
    TEST_PODMAN_INFO_FAIL="${TEST_PODMAN_INFO_FAIL:-0}" \
    /bin/bash "${INSTALLER}" "$@" 2>&1
  )"
  COMMAND_STATUS=$?
}

test_macos_fresh_install() {
  local init_command=$'podman\tmachine\tinit\t--rootful=false\t--update-connection=false\t--cpus\t2\t--memory\t4096\t--disk-size\t30\tpodman-machine-default'
  local start_command=$'podman\tmachine\tstart\t--update-connection=false\tpodman-machine-default'

  prepare_case macos-fresh
  enable_stub brew
  run_installer Darwin 501

  assert_status 0 "fresh macOS setup succeeds through isolated stubs"
  assert_logged_line $'brew\tinstall\tpodman' "fresh macOS setup installs the Podman formula"
  assert_logged_line "${init_command}" "fresh macOS setup initializes an explicitly rootless machine"
  assert_logged_line "${start_command}" "fresh macOS setup starts the new machine without changing the default connection"
  assert_logged_line $'podman\t--connection\tpodman-machine-default\tinfo' "fresh macOS setup verifies the named connection"
  assert_machine_state running false "fresh macOS setup leaves the machine running and rootless"
  assert_output_contains "Podman runtime is ready with rootless machine podman-machine-default" "fresh macOS setup reports readiness"
  assert_no_logged_command sudo "macOS setup never invokes sudo"
  assert_no_logged_command apt-get "macOS setup never invokes a Linux package manager"
  assert_no_unsafe_commands "fresh macOS setup records no destructive or rootful operation"
}

test_macos_existing_stopped() {
  prepare_case macos-stopped
  install_podman_stub
  enable_stub brew
  set_machine_state stopped false
  run_installer Darwin 501

  assert_status 0 "stopped rootless macOS machine is accepted"
  assert_no_logged_command brew "existing macOS Podman skips Homebrew"
  assert_no_podman_machine_action init "existing stopped machine is not reinitialized"
  assert_logged_line $'podman\tmachine\tstart\t--update-connection=false\tpodman-machine-default' "existing stopped machine is started"
  assert_machine_state running false "stopped machine reaches running without changing rootless mode"
  assert_no_unsafe_commands "stopped-machine setup records no destructive operation"
}

test_macos_existing_running() {
  prepare_case macos-running
  install_podman_stub
  enable_stub brew
  set_machine_state running false
  run_installer Darwin 501

  assert_status 0 "running rootless macOS machine is idempotent"
  assert_no_logged_command brew "running macOS machine skips Homebrew"
  assert_no_podman_machine_action init "running macOS machine is not reinitialized"
  assert_no_podman_machine_action start "running macOS machine is not restarted"
  assert_logged_line $'podman\t--connection\tpodman-machine-default\tinfo' "running macOS machine is still verified"
  assert_machine_state running false "running machine state is unchanged"
  assert_no_unsafe_commands "running-machine setup records no destructive operation"
}

test_macos_rootful_refusal() {
  prepare_case macos-rootful
  install_podman_stub
  set_machine_state stopped true
  run_installer Darwin 501

  assert_failure "existing rootful macOS machine is rejected"
  assert_output_contains "is rootful; refusing to change or use it" "rootful refusal explains the safety boundary"
  assert_no_podman_machine_action init "rootful machine is not replaced"
  assert_no_podman_machine_action start "rootful machine is not started"
  assert_no_logged_line $'podman\t--connection\tpodman-machine-default\tinfo' "rootful machine is not used for verification"
  assert_machine_state stopped true "rootful refusal preserves the existing machine"
  assert_no_unsafe_commands "rootful refusal does not try to switch, reset, or remove the machine"
}

test_linux_native_runtime() {
  prepare_case linux-native
  install_podman_stub
  run_installer Linux 1000

  assert_status 0 "existing native Linux Podman succeeds"
  assert_logged_line $'podman\tinfo' "native Linux Podman is verified"
  assert_no_logged_command sudo "existing native Linux Podman requires no privilege escalation"
  assert_no_logged_command apt-get "existing native Linux Podman performs no package install"
  assert_no_podman_machine_action init "native Linux setup never initializes a VM"
  assert_output_contains "native Podman runtime is ready" "native Linux setup reports readiness"
  assert_no_unsafe_commands "native Linux verification records no unsafe operation"
}

test_linux_package_installers() {
  prepare_case linux-apt
  enable_stub sudo
  enable_stub apt-get
  run_installer Linux 1000

  assert_status 0 "missing Linux Podman installs through apt-get"
  assert_logged_line $'sudo\tapt-get\tupdate' "apt setup refreshes package metadata through sudo"
  assert_logged_line $'sudo\tapt-get\tinstall\t-y\tpodman' "apt setup installs only Podman"
  assert_logged_line $'podman\tinfo' "apt-installed native Podman is verified"
  assert_no_logged_command brew "Linux apt setup never invokes Homebrew"
  assert_no_podman_machine_action init "Linux apt setup never initializes a VM"
  assert_no_unsafe_commands "Linux apt setup records only allowlisted package operations"

  prepare_case linux-dnf
  enable_stub sudo
  enable_stub dnf
  run_installer Linux 1000

  assert_status 0 "missing Linux Podman installs through dnf"
  assert_logged_line $'sudo\tdnf\tinstall\t-y\tpodman' "dnf setup installs only Podman"
  assert_logged_line $'podman\tinfo' "dnf-installed native Podman is verified"
  assert_no_logged_command apt-get "dnf setup does not fall through to apt-get"
  assert_no_podman_machine_action init "Linux dnf setup never initializes a VM"
  assert_no_unsafe_commands "Linux dnf setup records only allowlisted package operations"
}

test_dry_run_is_non_mutating() {
  prepare_case dry-run-macos
  enable_stub brew
  run_installer Darwin 501 --dry-run --smoke-test

  assert_status 0 "macOS dry-run succeeds without Podman installed"
  assert_output_contains "plan: brew install podman" "macOS dry-run shows the formula install"
  assert_output_contains "plan: podman machine init" "macOS dry-run shows rootless machine initialization"
  assert_output_contains "plan: podman --connection podman-machine-default run" "macOS dry-run shows the optional smoke test"
  assert_no_logged_command brew "macOS dry-run does not execute Homebrew"
  assert_no_logged_command podman "macOS dry-run does not execute Podman"
  if [[ ! -e "${ACTIVE_BIN}/podman" ]]; then
    pass "macOS dry-run does not install a CLI stub"
  else
    fail_test "macOS dry-run does not install a CLI stub"
  fi
  assert_machine_state absent false "macOS dry-run makes no machine state change"
  assert_no_unsafe_commands "macOS dry-run executes no unsafe operation"

  prepare_case dry-run-linux
  enable_stub sudo
  enable_stub apt-get
  run_installer Linux 1000 --dry-run

  assert_status 0 "Linux dry-run succeeds without Podman installed"
  assert_output_contains "plan: sudo apt-get update" "Linux dry-run shows metadata refresh"
  assert_output_contains "plan: sudo apt-get install -y podman" "Linux dry-run shows the Podman package install"
  assert_no_logged_command sudo "Linux dry-run does not execute sudo"
  assert_no_logged_command apt-get "Linux dry-run does not execute apt-get"
  assert_no_logged_command podman "Linux dry-run does not execute Podman"
  if [[ ! -e "${ACTIVE_BIN}/podman" ]]; then
    pass "Linux dry-run does not install a CLI stub"
  else
    fail_test "Linux dry-run does not install a CLI stub"
  fi
  assert_no_unsafe_commands "Linux dry-run executes no unsafe operation"
}

test_no_install_and_missing_manager() {
  prepare_case no-install-macos
  enable_stub brew
  run_installer Darwin 501 --no-install

  assert_failure "macOS --no-install rejects a missing Podman CLI"
  assert_output_contains "Podman is not installed and --no-install was passed" "macOS --no-install reports the missing prerequisite"
  assert_no_logged_command brew "macOS --no-install never invokes Homebrew"
  assert_no_logged_command podman "macOS --no-install never invokes Podman"

  prepare_case no-install-linux
  enable_stub sudo
  enable_stub apt-get
  run_installer Linux 1000 --no-install

  assert_failure "Linux --no-install rejects a missing Podman CLI"
  assert_no_logged_command sudo "Linux --no-install never invokes sudo"
  assert_no_logged_command apt-get "Linux --no-install never invokes a package manager"
  assert_no_logged_command podman "Linux --no-install never invokes Podman"

  prepare_case linux-no-manager
  enable_stub sudo
  run_installer Linux 1000

  assert_failure "Linux setup fails when no supported package manager is present"
  assert_output_contains "no supported Linux package manager found" "missing package-manager error is actionable"
  assert_no_logged_command sudo "missing package-manager failure occurs before sudo"
  assert_no_logged_command podman "missing package-manager failure never invokes Podman"
}

test_macos_homebrew_platform_gate() {
  prepare_case macos-intel
  enable_stub brew
  TEST_ARCH=x86_64
  run_installer Darwin 501
  unset TEST_ARCH

  assert_failure "Homebrew setup rejects an unsupported Intel Mac"
  assert_output_contains "Homebrew Podman requires Apple Silicon and macOS 13+" "Intel rejection identifies the Homebrew support boundary"
  assert_no_logged_command brew "Intel rejection occurs before Homebrew"
  assert_no_logged_command podman "Intel rejection never invokes Podman"

  prepare_case macos-old-version
  enable_stub brew
  TEST_MACOS_VERSION=12.7.6
  run_installer Darwin 501
  unset TEST_MACOS_VERSION

  assert_failure "Homebrew setup rejects macOS older than 13"
  assert_output_contains "install Podman's signed package" "older macOS is routed to the upstream installer"
  assert_no_logged_command brew "older macOS rejection occurs before Homebrew"
  assert_no_logged_command podman "older macOS rejection never invokes Podman"
}

test_input_validation() {
  prepare_case help
  run_installer Darwin 501 --help
  assert_status 0 "--help succeeds"
  assert_output_contains "Usage: install_podman_runtime.sh" "--help prints usage"
  if [[ ! -s "${COMMAND_LOG}" ]]; then
    pass "--help performs no platform or runtime probes"
  else
    fail_test "--help performs no platform or runtime probes"
  fi

  prepare_case invalid-machine-name
  run_installer Darwin 501 --machine-name ../unsafe
  assert_failure "unsafe machine name is rejected"
  assert_output_contains "unsafe Podman machine name" "unsafe machine-name error is specific"
  if [[ ! -s "${COMMAND_LOG}" ]]; then
    pass "unsafe machine name fails before external commands"
  else
    fail_test "unsafe machine name fails before external commands"
  fi

  prepare_case invalid-cpus
  run_installer Linux 1000 --cpus 0
  assert_failure "zero CPU count is rejected"
  assert_output_contains "--cpus must be a positive integer" "CPU validation error is specific"
  if [[ ! -s "${COMMAND_LOG}" ]]; then
    pass "invalid numeric input fails before external commands"
  else
    fail_test "invalid numeric input fails before external commands"
  fi

  prepare_case long-machine-name
  run_installer Darwin 501 --machine-name abcdefghijklmnopqrstuvwxyz12345
  assert_failure "overlong machine name is rejected"
  assert_output_contains "must be 30 characters or fewer" "machine-name length error is specific"
  if [[ ! -s "${COMMAND_LOG}" ]]; then
    pass "overlong machine name fails before external commands"
  else
    fail_test "overlong machine name fails before external commands"
  fi

  prepare_case unknown-option
  run_installer Linux 1000 --surprise
  assert_failure "unknown option is rejected"
  assert_output_contains "unknown argument: --surprise" "unknown-option error is specific"
  if [[ ! -s "${COMMAND_LOG}" ]]; then
    pass "unknown option fails before external commands"
  else
    fail_test "unknown option fails before external commands"
  fi

  prepare_case unsupported-platform
  run_installer FreeBSD 1000
  assert_failure "unsupported host platform is rejected"
  assert_output_contains "unsupported host platform: FreeBSD" "unsupported-platform error names the host"
  assert_no_logged_command brew "unsupported platform invokes no installer"
  assert_no_logged_command podman "unsupported platform invokes no runtime"
}

write_stubs

if [[ -f "${INSTALLER}" ]]; then
  pass "Podman runtime installer exists"
else
  fail_test "Podman runtime installer exists" "missing ${INSTALLER}"
fi

/bin/bash -n "${INSTALLER}"
COMMAND_STATUS=$?
assert_status 0 "Podman runtime installer parses under /bin/bash"
/bin/bash -n "${BASH_SOURCE[0]}"
COMMAND_STATUS=$?
assert_status 0 "Podman runtime test parses under /bin/bash"

test_macos_fresh_install
test_macos_existing_stopped
test_macos_existing_running
test_macos_rootful_refusal
test_linux_native_runtime
test_linux_package_installers
test_dry_run_is_non_mutating
test_no_install_and_missing_manager
test_macos_homebrew_platform_gate
test_input_validation

printf '# tests: %s passed, %s failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi
