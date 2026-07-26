#!/usr/bin/env bash
# Exercise one bootstrap tier in a fresh, networkless Linux container.

set -euo pipefail

TIER="${TRACTION_CONTROL_TEST_TIER:-}"
SOURCE_ROOT="/opt/repo-sources"
TEST_ROOT="/tmp/traction-control-${TIER:-unset}"
REMOTE_ROOT="${TEST_ROOT}/remotes"
REMOTE_WORK_ROOT="${TEST_ROOT}/remote-work"
PORTFOLIO_ROOT="${TEST_ROOT}/portfolio"
HOME_ROOT="${TEST_ROOT}/home"
XDG_CONFIG_ROOT="${TEST_ROOT}/xdg-config"
XDG_DATA_ROOT="${TEST_ROOT}/xdg-data"
STATE_ROOT="${TEST_ROOT}/state"
UNIT_ROOT="${TEST_ROOT}/units"
FAKE_BIN="${TEST_ROOT}/fake-bin"
SYSTEMCTL_SENTINEL_LOG="${TEST_ROOT}/systemctl-called.log"
BOOTSTRAP_INSTALLER="${SOURCE_ROOT}/traction-control/scripts/install_traction_control_agents.sh"
CHECK_COUNT=0

fail() {
  printf 'not ok %s - %s\n' "${TIER:-unknown}" "$*" >&2
  exit 1
}

pass() {
  CHECK_COUNT=$(( CHECK_COUNT + 1 ))
  printf 'ok %s.%s - %s\n' "${TIER}" "${CHECK_COUNT}" "$1"
}

assert_file_contains() {
  local file_path="$1"
  local expected="$2"
  local label="$3"
  [[ -f "${file_path}" ]] || fail "${label}: missing ${file_path}"
  grep -Fq -- "${expected}" "${file_path}" \
    || fail "${label}: ${file_path} does not contain ${expected}"
  pass "${label}"
}

assert_exact_directories() {
  local parent_path="$1"
  local label="$2"
  shift 2
  local actual_path="${TEST_ROOT}/actual-directories.txt"
  local expected_path="${TEST_ROOT}/expected-directories.txt"

  find "${parent_path}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | sort > "${actual_path}"
  printf '%s\n' "$@" | sort > "${expected_path}"
  if ! cmp -s "${actual_path}" "${expected_path}"; then
    diff -u "${expected_path}" "${actual_path}" >&2 || true
    fail "${label}: directory set differs"
  fi
  pass "${label}"
}

assert_exact_files() {
  local parent_path="$1"
  local label="$2"
  shift 2
  local actual_path="${TEST_ROOT}/actual-files.txt"
  local expected_path="${TEST_ROOT}/expected-files.txt"

  find "${parent_path}" -mindepth 1 -maxdepth 1 -type f -printf '%f\n' \
    | sort > "${actual_path}"
  printf '%s\n' "$@" | sort > "${expected_path}"
  if ! cmp -s "${actual_path}" "${expected_path}"; then
    diff -u "${expected_path}" "${actual_path}" >&2 || true
    fail "${label}: file set differs"
  fi
  pass "${label}"
}

snapshot_install_artifacts() {
  local output_path="$1"
  shift
  local artifact_name=""
  local artifact_path=""

  : > "${output_path}"
  for artifact_name in "$@"; do
    artifact_path="${UNIT_ROOT}/${artifact_name}"
    stat -c '%a %n' "${artifact_path}" >> "${output_path}"
    sha256sum "${artifact_path}" >> "${output_path}"
  done
  if [[ -e "${STATE_ROOT}/bin/archility" ]]; then
    stat -c '%a %n' "${STATE_ROOT}/bin/archility" >> "${output_path}"
    sha256sum "${STATE_ROOT}/bin/archility" >> "${output_path}"
  fi
}

case "${TIER}" in
  light|moderate|heavy) ;;
  *) fail "TRACTION_CONTROL_TEST_TIER must be light, moderate, or heavy" ;;
esac

REPOSITORIES=(traction-control clockwork)
SERVICES=(
  portfolio-audit-daily
  bug-sweep-agentic
  ci-repair-agentic-discovery
)
TIMERS=(
  portfolio-audit-daily
  bug-sweep-agentic
  ci-repair-agentic-discovery
)

if [[ "${TIER}" == "moderate" || "${TIER}" == "heavy" ]]; then
  REPOSITORIES+=(archility tachometer)
  SERVICES+=(
    archility-daily
    archility-weekly
    template-consolidation-agentic
    refs-audit-agentic
  )
  TIMERS+=(
    archility-daily
    archility-weekly
    template-consolidation-agentic
    refs-audit-agentic
  )
fi

if [[ "${TIER}" == "heavy" ]]; then
  REPOSITORIES+=(auto-pass shock-relay)
  SERVICES+=(ci-repair-agentic-repair tachometer-disk-pressure-agentic)
  TIMERS+=(tachometer-disk-pressure-agentic)
fi

ALL_REPOSITORIES=(
  traction-control
  clockwork
  archility
  tachometer
  auto-pass
  shock-relay
)

mkdir -p \
  "${REMOTE_ROOT}" \
  "${REMOTE_WORK_ROOT}" \
  "${PORTFOLIO_ROOT}" \
  "${HOME_ROOT}" \
  "${XDG_CONFIG_ROOT}" \
  "${XDG_DATA_ROOT}" \
  "${FAKE_BIN}"

export HOME="${HOME_ROOT}"
export XDG_CONFIG_HOME="${XDG_CONFIG_ROOT}"
export XDG_DATA_HOME="${XDG_DATA_ROOT}"
export SYSTEMCTL_SENTINEL_LOG

git config --global user.email 'container-test@example.invalid'
git config --global user.name 'Traction Control Container Test'
git config --global protocol.file.allow always

for repository_name in "${ALL_REPOSITORIES[@]}"; do
  source_path="${SOURCE_ROOT}/${repository_name}"
  work_path="${REMOTE_WORK_ROOT}/${repository_name}"
  bare_path="${REMOTE_ROOT}/${repository_name}.git"
  [[ -d "${source_path}" ]] || fail "missing staged source for ${repository_name}"
  mkdir -p "${work_path}"
  cp -R "${source_path}/." "${work_path}/"
  git -C "${work_path}" init --quiet --initial-branch=main
  git -C "${work_path}" add .
  git -C "${work_path}" commit --quiet -m 'container fixture'
  git clone --quiet --bare "${work_path}" "${bare_path}"
done
pass "created six local Git remotes from the staged repositories"

git config --global \
  "url.file://${REMOTE_ROOT}/.insteadOf" \
  'https://github.com/casonk/'

for command_name in codex gh jq; do
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "${FAKE_BIN}/${command_name}"
  chmod 0755 "${FAKE_BIN}/${command_name}"
done

cat > "${FAKE_BIN}/systemctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SYSTEMCTL_SENTINEL_LOG:?}"
exit 97
EOF
chmod 0755 "${FAKE_BIN}/systemctl"
export PATH="${FAKE_BIN}:/usr/local/bin:/usr/bin:/bin"

if ! download_output="$(
  bash "${BOOTSTRAP_INSTALLER}" \
    --tier "${TIER}" \
    --portfolio-root "${PORTFOLIO_ROOT}" \
    --platform linux \
    --no-scheduler \
    2>&1
)"; then
  printf '%s\n' "${download_output}" >&2
  fail "support-repository download failed"
fi
printf '%s\n' "${download_output}"
pass "downloaded the ${TIER} support-repository bundle"

git config --global --unset-all "url.file://${REMOTE_ROOT}/.insteadOf"

assert_exact_directories \
  "${PORTFOLIO_ROOT}/util-repos" \
  "cloned the exact cumulative repository set" \
  "${REPOSITORIES[@]}"

for repository_name in "${REPOSITORIES[@]}"; do
  expected_origin="https://github.com/casonk/${repository_name}.git"
  actual_origin="$(
    git -C "${PORTFOLIO_ROOT}/util-repos/${repository_name}" \
      remote get-url origin
  )"
  [[ "${actual_origin}" == "${expected_origin}" ]] \
    || fail "origin mismatch for ${repository_name}: ${actual_origin}"
done
pass "all cloned repositories retain their allowlisted GitHub origins"

INSTALLER="${PORTFOLIO_ROOT}/util-repos/traction-control/scripts/install_traction_control_agents.sh"
[[ -x "${INSTALLER}" ]] || fail "cloned installer is not executable: ${INSTALLER}"

if ! install_output="$(
  bash "${INSTALLER}" \
    --tier "${TIER}" \
    --portfolio-root "${PORTFOLIO_ROOT}" \
    --platform linux \
    --provider codex \
    --model container-test \
    --no-clone \
    --state-dir "${STATE_ROOT}" \
    --systemd-unit-dir "${UNIT_ROOT}" \
    2>&1
)"; then
  printf '%s\n' "${install_output}" >&2
  fail "render-only installation failed"
fi
printf '%s\n' "${install_output}"
case "${install_output}" in
  *"${TIER} profile rendered but left inactive (${#SERVICES[@]} jobs)"*) ;;
  *) fail "installer did not report the expected ${#SERVICES[@]}-job profile" ;;
esac
pass "rendered the expected ${#SERVICES[@]}-job profile"

[[ ! -e "${SYSTEMCTL_SENTINEL_LOG}" ]] \
  || fail "render-only installation invoked systemctl"
pass "render-only installation made no systemctl calls"

EXPECTED_ARTIFACTS=()
for service_name in "${SERVICES[@]}"; do
  EXPECTED_ARTIFACTS+=("${service_name}.service")
done
for timer_name in "${TIMERS[@]}"; do
  EXPECTED_ARTIFACTS+=("${timer_name}.timer")
done
assert_exact_files \
  "${UNIT_ROOT}" \
  "rendered the exact service/timer artifact set" \
  "${EXPECTED_ARTIFACTS[@]}"

for service_name in "${SERVICES[@]}"; do
  assert_file_contains \
    "${UNIT_ROOT}/${service_name}.service" \
    'Type=oneshot' \
    "${service_name} is a oneshot service"
done
for timer_name in "${TIMERS[@]}"; do
  assert_file_contains \
    "${UNIT_ROOT}/${timer_name}.timer" \
    'WantedBy=timers.target' \
    "${timer_name} is installable by timers.target"
done

assert_file_contains \
  "${UNIT_ROOT}/bug-sweep-agentic.service" \
  'BUG_SWEEP_AGENTIC_PROVIDER=codex' \
  "provider selection reached the rendered units"
assert_file_contains \
  "${UNIT_ROOT}/bug-sweep-agentic.service" \
  'BUG_SWEEP_AGENTIC_MODEL=container-test' \
  "model selection reached the rendered units"
assert_file_contains \
  "${UNIT_ROOT}/portfolio-audit-daily.service" \
  "PORTFOLIO_ROOT=${PORTFOLIO_ROOT}" \
  "container portfolio root reached the rendered units"
assert_file_contains \
  "${UNIT_ROOT}/portfolio-audit-daily.service" \
  "WorkingDirectory=${PORTFOLIO_ROOT}/util-repos/traction-control" \
  "services run from the cloned traction-control repository"
assert_file_contains \
  "${UNIT_ROOT}/ci-repair-agentic-discovery.service" \
  '--discovery-only' \
  "default CI scheduling remains discovery-only"
assert_file_contains \
  "${UNIT_ROOT}/portfolio-audit-daily.timer" \
  'OnCalendar=*-*-* 08:00:00' \
  "daily portfolio schedule survived Clockwork rendering"
assert_file_contains \
  "${UNIT_ROOT}/bug-sweep-agentic.timer" \
  'OnUnitActiveSec=1d' \
  "daily bug-sweep interval survived Clockwork rendering"
assert_file_contains \
  "${UNIT_ROOT}/ci-repair-agentic-discovery.timer" \
  'OnUnitActiveSec=2d' \
  "discovery interval survived Clockwork rendering"

if find "${UNIT_ROOT}" -mindepth 1 \( -type d -o -type l \) -print -quit \
  | grep -q .; then
  fail "render-only installation created activation directories or symlinks"
fi
pass "render-only installation created no activation symlinks"

if grep -R -E '__[A-Z][A-Z0-9_]*__' "${UNIT_ROOT}" >/dev/null 2>&1; then
  fail "rendered units contain an unresolved template placeholder"
fi
pass "rendered units contain no unresolved template placeholders"

if [[ "${TIER}" == "light" ]]; then
  [[ ! -e "${STATE_ROOT}/bin/archility" ]] \
    || fail "light unexpectedly created an Archility shim"
  pass "light does not create an Archility shim"
else
  [[ -x "${STATE_ROOT}/bin/archility" ]] \
    || fail "${TIER} did not create an executable Archility shim"
  assert_file_contains \
    "${UNIT_ROOT}/archility-daily.service" \
    "ARCHILITY_CMD=${STATE_ROOT}/bin/archility" \
    "${TIER} points Archility jobs at the generated shim"
  assert_file_contains \
    "${UNIT_ROOT}/archility-daily.timer" \
    'OnCalendar=*-*-* 02:00:00' \
    "${TIER} preserves the daily architecture schedule"
  assert_file_contains \
    "${UNIT_ROOT}/archility-weekly.timer" \
    'OnCalendar=Wed,Sun *-*-* 03:00:00' \
    "${TIER} preserves the twice-weekly architecture schedule"
  assert_file_contains \
    "${UNIT_ROOT}/refs-audit-agentic.timer" \
    'OnCalendar=Thu *-*-* 03:00:00' \
    "${TIER} preserves the weekly REFS schedule"
fi

if [[ "${TIER}" == "heavy" ]]; then
  [[ -f "${UNIT_ROOT}/ci-repair-agentic-repair.service" ]] \
    || fail "heavy repair service is missing"
  [[ ! -e "${UNIT_ROOT}/ci-repair-agentic-repair.timer" ]] \
    || fail "heavy repair service unexpectedly has a timer"
  [[ ! -e "${UNIT_ROOT}/ci-repair-agentic.service" ]] \
    || fail "default heavy unexpectedly contains autonomous CI repair"
  assert_file_contains \
    "${UNIT_ROOT}/ci-repair-agentic-repair.service" \
    '--candidate-file' \
    "heavy keeps broad CI repair operator-triggered"
  assert_file_contains \
    "${UNIT_ROOT}/tachometer-disk-pressure-agentic.timer" \
    'OnUnitActiveSec=6h' \
    "heavy preserves the disk-pressure interval"
fi

VERIFY_ROOT="${TEST_ROOT}/verify-units"
VERIFY_RUNTIME_ROOT="${TEST_ROOT}/verify-runtime"
mkdir -p "${VERIFY_ROOT}"
mkdir -m 0700 "${VERIFY_RUNTIME_ROOT}"
cp "${UNIT_ROOT}"/* "${VERIFY_ROOT}/"
[[ ! -e "${VERIFY_RUNTIME_ROOT}/bus" ]] \
  || fail "unit verification runtime unexpectedly contains a user-manager bus"
if ! env -u DBUS_SESSION_BUS_ADDRESS \
  XDG_RUNTIME_DIR="${VERIFY_RUNTIME_ROOT}" \
  SYSTEMD_UNIT_PATH="${VERIFY_ROOT}:/usr/lib/systemd/user" \
  systemd-analyze \
    --user \
    --generators=no \
    --man=no \
    --recursive-errors=yes \
    verify \
    "${VERIFY_ROOT}"/*.service \
    "${VERIFY_ROOT}"/*.timer \
    > "${TEST_ROOT}/systemd-analyze.log" 2>&1; then
  sed 's/^/systemd-analyze: /' "${TEST_ROOT}/systemd-analyze.log" >&2
  fail "systemd-analyze rejected the rendered units"
fi
[[ ! -e "${VERIFY_RUNTIME_ROOT}/bus" ]] \
  || fail "systemd-analyze verification created a user-manager bus"
pass "systemd-analyze accepted every rendered service and timer without a live user-manager bus"

BEFORE_RERUN_SNAPSHOT="${TEST_ROOT}/before-rerun.sha256"
AFTER_RERUN_SNAPSHOT="${TEST_ROOT}/after-rerun.sha256"
snapshot_install_artifacts "${BEFORE_RERUN_SNAPSHOT}" "${EXPECTED_ARTIFACTS[@]}"
printf 'preserve rerun marker\n' \
  > "${PORTFOLIO_ROOT}/util-repos/clockwork/.container-rerun-marker"
if ! rerun_output="$(
  bash "${INSTALLER}" \
    --tier "${TIER}" \
    --portfolio-root "${PORTFOLIO_ROOT}" \
    --platform linux \
    --provider codex \
    --model container-test \
    --no-clone \
    --state-dir "${STATE_ROOT}" \
    --systemd-unit-dir "${UNIT_ROOT}" \
    2>&1
)"; then
  printf '%s\n' "${rerun_output}" >&2
  fail "idempotent rerun failed"
fi
[[ -f "${PORTFOLIO_ROOT}/util-repos/clockwork/.container-rerun-marker" ]] \
  || fail "rerun changed existing repository contents"
assert_exact_files \
  "${UNIT_ROOT}" \
  "idempotent rerun preserves the exact artifact set" \
  "${EXPECTED_ARTIFACTS[@]}"
snapshot_install_artifacts "${AFTER_RERUN_SNAPSHOT}" "${EXPECTED_ARTIFACTS[@]}"
if ! cmp -s "${BEFORE_RERUN_SNAPSHOT}" "${AFTER_RERUN_SNAPSHOT}"; then
  diff -u "${BEFORE_RERUN_SNAPSHOT}" "${AFTER_RERUN_SNAPSHOT}" >&2 || true
  fail "idempotent rerun changed unit or Archility shim contents/modes"
fi
pass "idempotent rerun preserves unit and Archility shim contents/modes"
[[ ! -e "${SYSTEMCTL_SENTINEL_LOG}" ]] \
  || fail "render-only rerun invoked systemctl"
pass "rerun verifies origins without recloning or invoking systemctl"

rm -f "${FAKE_BIN}/systemctl"
ACTIVATION_HOME="${TEST_ROOT}/activation-home"
ACTIVATION_CONFIG="${TEST_ROOT}/activation-config"
ACTIVATION_DATA="${TEST_ROOT}/activation-data"
ACTIVATION_STATE="${TEST_ROOT}/activation-state"
mkdir -p "${ACTIVATION_HOME}" "${ACTIVATION_CONFIG}" "${ACTIVATION_DATA}"

if activation_output="$(
  HOME="${ACTIVATION_HOME}" \
  XDG_CONFIG_HOME="${ACTIVATION_CONFIG}" \
  XDG_DATA_HOME="${ACTIVATION_DATA}" \
  bash "${INSTALLER}" \
    --tier "${TIER}" \
    --portfolio-root "${PORTFOLIO_ROOT}" \
    --platform linux \
    --provider codex \
    --model container-test \
    --no-clone \
    --activate \
    --state-dir "${ACTIVATION_STATE}" \
    2>&1
)"; then
  fail "activation unexpectedly succeeded without a systemd user manager"
fi
case "${activation_output}" in
  *'systemd user manager is unavailable'*) ;;
  *)
    printf '%s\n' "${activation_output}" >&2
    fail "activation refusal did not report the missing user manager"
    ;;
esac
[[ ! -e "${ACTIVATION_STATE}" ]] \
  || fail "failed activation created bootstrap state"
[[ ! -e "${ACTIVATION_CONFIG}/systemd/user" ]] \
  || fail "failed activation wrote live user units"
pass "activation fails safely before writes when no user manager is available"

printf 'PASS %s: %s container checks\n' "${TIER}" "${CHECK_COUNT}"
