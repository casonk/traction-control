#!/usr/bin/env bash
# Run inside the disposable Vagrant guest as its ordinary non-root user.
set -euo pipefail

REPO_ROOT="/portfolio/util-repos/traction-control"
TEST_ROOT="$(mktemp -d /tmp/traction-control-live-systemd.XXXXXX)"
UNIT_ROOT="${HOME}/.config/systemd/user"
RUNTIME_CONTROL_ROOT="${XDG_RUNTIME_DIR}/systemd/user.control"
STATE_ROOT="${TEST_ROOT}/state"
TEST_BIN="${TEST_ROOT}/bin"
REPO_CONFIG="${REPO_ROOT}/tests/vagrant/repos.conf"
JOB_CONFIG="${REPO_ROOT}/tests/vagrant/jobs.conf"
INSTALLER="${REPO_ROOT}/scripts/install_traction_control_agents.sh"
JOBS=(live-light ci-repair-agentic-discovery ci-repair-agentic)

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

cleanup() {
  local job=""
  for job in "${JOBS[@]}"; do
    systemctl --user disable --now "${job}.timer" >/dev/null 2>&1 || true
    rm -f "${RUNTIME_CONTROL_ROOT}/${job}.service"
    rm -f "${UNIT_ROOT}/${job}.service" "${UNIT_ROOT}/${job}.timer"
  done
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -rf "${TEST_ROOT}"
}
trap cleanup EXIT HUP INT TERM

systemctl --user show-environment >/dev/null \
  || fail 'systemd user manager is unavailable'
for job in "${JOBS[@]}"; do
  [[ ! -e "${UNIT_ROOT}/${job}.service" && ! -e "${UNIT_ROOT}/${job}.timer" ]] \
    || fail "refusing to replace existing fixture unit ${job}"
done

run_profile() {
  local job=""
  for job in "${JOBS[@]}"; do
    rm -f "${RUNTIME_CONTROL_ROOT}/${job}.service"
  done
  systemctl --user daemon-reload
  PATH="${TEST_BIN}:${PATH}" bash "${INSTALLER}" \
    --tier "$1" \
    --portfolio-root /portfolio \
    --platform linux \
    --provider codex \
    --model live-systemd-test \
    --no-clone \
    --activate \
    --state-dir "${STATE_ROOT}" \
    --repo-config "${REPO_CONFIG}" \
    --job-config "${JOB_CONFIG}" \
    "${@:2}"
  mkdir -p "${RUNTIME_CONTROL_ROOT}"
  for job in "${JOBS[@]}"; do
    ln -s /dev/null "${RUNTIME_CONTROL_ROOT}/${job}.service"
  done
  systemctl --user daemon-reload
}

mkdir -p "${TEST_BIN}"
printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "${TEST_BIN}/gh"
chmod 0700 "${TEST_BIN}/gh"

assert_timer_set() {
  local label="$1"
  shift
  local expected='|'
  local actual_enabled='|'
  local actual_active='|'
  local job=""
  for job in "$@"; do
    expected+="${job}|"
  done
  for job in "${JOBS[@]}"; do
    if systemctl --user is-enabled --quiet "${job}.timer"; then
      actual_enabled+="${job}|"
    fi
    if systemctl --user is-active --quiet "${job}.timer"; then
      actual_active+="${job}|"
    fi
    case "$(systemctl --user is-enabled "${job}.service" 2>/dev/null || true)" in
      masked|masked-runtime) ;;
      *) fail "${label}: workload service ${job} is not runtime-masked" ;;
    esac
  done
  [[ "${actual_enabled}" == "${expected}" ]] \
    || fail "${label}: enabled timers ${actual_enabled}, expected ${expected}"
  [[ "${actual_active}" == "${expected}" ]] \
    || fail "${label}: active timers ${actual_active}, expected ${expected}"
  printf 'ok - %s\n' "${label}"
}

run_profile heavy
assert_timer_set 'normal heavy activation' live-light ci-repair-agentic-discovery
run_profile heavy --enable-autonomous-ci-repair
assert_timer_set 'normal-to-autonomous reconciliation' live-light ci-repair-agentic
run_profile light
assert_timer_set 'heavy-to-light reconciliation' live-light
printf '%s\n' 'live systemd activation fixture passed without running a workload'
