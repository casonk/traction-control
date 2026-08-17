#!/usr/bin/env bash
# Isolated integration coverage for the tiered traction-control bootstrap.
#
# The test suite deliberately replaces commands that could reach GitHub or a
# live scheduler. All generated repositories, units, plists, and workload logs
# stay below a temporary directory.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALLER="${REPO_ROOT}/scripts/install_traction_control_agents.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/traction-control-tests.XXXXXX")"
FAKE_BIN="${TEST_ROOT}/fake-bin"
COMMAND_LOG="${TEST_ROOT}/commands.log"
ORIGINAL_PATH="${PATH}"

PASS_COUNT=0
FAIL_COUNT=0
COMMAND_OUTPUT=""
COMMAND_STATUS=0

cleanup() {
  case "${TEST_ROOT}" in
    "${TMPDIR:-/tmp}"/traction-control-tests.*)
      rm -rf "${TEST_ROOT}"
      ;;
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

run_command() {
  COMMAND_OUTPUT="$("$@" 2>&1)"
  COMMAND_STATUS=$?
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

assert_output_contains() {
  local expected="$1"
  local label="$2"
  case "${COMMAND_OUTPUT}" in
    *"${expected}"*) pass "${label}" ;;
    *) fail_test "${label}" "missing output: ${expected}" ;;
  esac
}

assert_file_contains() {
  local path="$1"
  local expected="$2"
  local label="$3"
  if [[ -f "${path}" ]] && grep -Fq -- "${expected}" "${path}"; then
    pass "${label}"
  else
    fail_test "${label}" "${path} does not contain: ${expected}"
  fi
}

assert_file_exists() {
  local path="$1"
  local label="$2"
  if [[ -f "${path}" ]]; then
    pass "${label}"
  else
    fail_test "${label}" "missing file: ${path}"
  fi
}

assert_file_not_contains() {
  local path="$1"
  local unexpected="$2"
  local label="$3"
  if [[ -f "${path}" ]] && ! grep -Fq -- "${unexpected}" "${path}"; then
    pass "${label}"
  else
    fail_test "${label}" "${path} unexpectedly contains: ${unexpected}"
  fi
}

assert_no_logged_command() {
  local command_name="$1"
  local label="$2"
  if ! grep -q "^${command_name}[[:space:]]" "${COMMAND_LOG}" 2>/dev/null; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected ${command_name} invocation recorded"
  fi
}

assert_line_sets_equal() {
  local actual_path="$1"
  local expected_path="$2"
  local label="$3"
  if cmp -s "${actual_path}" "${expected_path}"; then
    pass "${label}"
  else
    fail_test "${label}" "expected:\n$(sed 's/^/    /' "${expected_path}")\n  actual:\n$(sed 's/^/    /' "${actual_path}")"
  fi
}

write_command_stubs() {
  mkdir -p "${FAKE_BIN}"
  : > "${COMMAND_LOG}"

  cat > "${FAKE_BIN}/git" <<'STUB'
#!/usr/bin/env bash
set -u
printf 'git' >> "${TEST_COMMAND_LOG}"
for arg in "$@"; do
  printf '\t%s' "${arg}" >> "${TEST_COMMAND_LOG}"
done
printf '\n' >> "${TEST_COMMAND_LOG}"

if [[ "${1:-}" == "clone" ]]; then
  target=""
  for arg in "$@"; do
    target="${arg}"
  done
  mkdir -p "${target}/.git"
  printf 'ref: refs/heads/main\n' > "${target}/.git/HEAD"
  exit 0
fi

if [[ "${1:-}" == "-C" ]]; then
  target="${2:-}"
  operation="${3:-}"
  case "${operation}" in
    rev-parse)
      [[ -d "${target}/.git" ]] || exit 1
      if [[ "${4:-}" == "--show-toplevel" ]]; then
        if [[ -f "${target}/.fake-git-top" ]]; then
          sed -n '1p' "${target}/.fake-git-top"
        else
          printf '%s\n' "${target}"
        fi
      else
        printf 'true\n'
      fi
      ;;
    remote)
      repo_name="$(basename "${target}")"
      printf 'https://github.com/casonk/%s.git\n' "${repo_name}"
      ;;
    symbolic-ref)
      printf 'origin/main\n'
      ;;
    show-ref|status)
      ;;
    ls-files)
      case " $* " in
        *" --error-unmatch REFS-PUBLIC.md "*) printf 'REFS-PUBLIC.md\n' ;;
        *) printf 'sample.py\n' ;;
      esac
      ;;
  esac
fi
STUB

  cat > "${FAKE_BIN}/clockwork" <<'STUB'
#!/usr/bin/env bash
set -u
printf 'clockwork' >> "${TEST_COMMAND_LOG}"
for arg in "$@"; do
  printf '\t%s' "${arg}" >> "${TEST_COMMAND_LOG}"
done
printf '\n' >> "${TEST_COMMAND_LOG}"

if [[ -n "${TEST_CLOCKWORK_CALL_COUNT_FILE:-}" ]]; then
  printf 'call\n' >> "${TEST_CLOCKWORK_CALL_COUNT_FILE}"
  call_count="$(wc -l < "${TEST_CLOCKWORK_CALL_COUNT_FILE}" | tr -d ' ')"
  if [[ -n "${TEST_CLOCKWORK_FAIL_N:-}" && "${call_count}" == "${TEST_CLOCKWORK_FAIL_N}" ]]; then
    exit 42
  fi
fi

manifest=""
unit_dir=""
target=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) manifest="$2"; shift 2 ;;
    --unit-dir) unit_dir="$2"; shift 2 ;;
    --target) target="$2"; shift 2 ;;
    *) shift ;;
  esac
done

job_name="$(sed -n 's/^name = "\([^"]*\)"/\1/p' "${manifest}" | head -n 1)"
mkdir -p "${unit_dir}"
if [[ "${target}" == "launchd-user" ]]; then
  label="$(sed -n 's/^launchd_label = "\([^"]*\)"/\1/p' "${manifest}" | head -n 1)"
  payload_label="${label}"
  if [[ -n "${TEST_CLOCKWORK_MISLABEL_JOB:-}" && "${job_name}" == "${TEST_CLOCKWORK_MISLABEL_JOB}" ]]; then
    payload_label="io.github.casonk.traction-control.wrong-${job_name}"
  fi
  interval="$(sed -n 's/^on_unit_active_sec = "\([0-9]*\)s"/\1/p' "${manifest}" | head -n 1)"
  model="$(sed -n 's/^[A-Z][A-Z0-9_]*_MODEL = "\(.*\)"/\1/p' "${manifest}" | head -n 1)"
  model="${model//&/\&amp;}"
  {
    printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>'
    printf '%s\n' '<plist version="1.0"><dict>'
    printf '<key>Label</key><string>%s</string>\n' "${payload_label}"
    printf '%s\n' '<key>ProgramArguments</key><array><string>/bin/echo</string></array>'
    if [[ -n "${interval}" ]]; then
      printf '<key>StartInterval</key><integer>%s</integer>\n' "${interval}"
    fi
    if [[ -n "${model}" ]]; then
      printf '<key>TEST_MODEL</key><string>%s</string>\n' "${model}"
    fi
    printf '%s\n' '</dict></plist>'
  } > "${unit_dir}/${label}.plist"
  exit 0
fi
cp "${manifest}" "${unit_dir}/${job_name}.service"
if grep -Fq '[jobs.timer]' "${manifest}"; then
  printf '[Timer]\nUnit=%s.service\n' "${job_name}" > "${unit_dir}/${job_name}.timer"
fi
STUB

  cat > "${FAKE_BIN}/systemctl" <<'STUB'
#!/usr/bin/env bash
printf 'systemctl' >> "${TEST_COMMAND_LOG}"
for arg in "$@"; do printf '\t%s' "${arg}" >> "${TEST_COMMAND_LOG}"; done
printf '\n' >> "${TEST_COMMAND_LOG}"
case " $* " in
  *" is-active "*) exit 3 ;;
  *" --property=LoadState "*) printf 'not-found\n'; exit 0 ;;
  *" --property=ActiveState "*) printf 'inactive\n'; exit 0 ;;
esac
if [[ -n "${TEST_SYSTEMCTL_DISABLE_FAIL_UNIT:-}" ]] \
  && [[ " $* " == *" disable --now ${TEST_SYSTEMCTL_DISABLE_FAIL_UNIT} "* ]]; then
  exit 1
fi
exit 0
STUB

  cat > "${FAKE_BIN}/launchctl" <<'STUB'
#!/usr/bin/env bash
printf 'launchctl' >> "${TEST_COMMAND_LOG}"
for arg in "$@"; do printf '\t%s' "${arg}" >> "${TEST_COMMAND_LOG}"; done
printf '\n' >> "${TEST_COMMAND_LOG}"

state_contains() {
  [[ -n "${TEST_LAUNCHCTL_STATE_FILE:-}" && -f "${TEST_LAUNCHCTL_STATE_FILE}" ]] \
    && grep -Fxq -- "$1" "${TEST_LAUNCHCTL_STATE_FILE}"
}

state_add() {
  [[ -n "${TEST_LAUNCHCTL_STATE_FILE:-}" ]] || return 0
  touch "${TEST_LAUNCHCTL_STATE_FILE}"
  if ! grep -Fxq -- "$1" "${TEST_LAUNCHCTL_STATE_FILE}"; then
    printf '%s\n' "$1" >> "${TEST_LAUNCHCTL_STATE_FILE}"
  fi
}

state_remove() {
  local temporary=""
  [[ -n "${TEST_LAUNCHCTL_STATE_FILE:-}" ]] || return 0
  touch "${TEST_LAUNCHCTL_STATE_FILE}"
  temporary="${TEST_LAUNCHCTL_STATE_FILE}.tmp"
  grep -Fxv -- "$1" "${TEST_LAUNCHCTL_STATE_FILE}" > "${temporary}" || true
  mv "${temporary}" "${TEST_LAUNCHCTL_STATE_FILE}"
}

override_set() {
  local label="$1"
  local value="$2"
  local temporary=""
  [[ -n "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE:-}" ]] || return 0
  touch "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE}"
  temporary="${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE}.tmp"
  grep -Fv -- "${label}=" "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE}" > "${temporary}" || true
  printf '%s=%s\n' "${label}" "${value}" >> "${temporary}"
  mv "${temporary}" "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE}"
}

case "${1:-}" in
  print)
    target="${2:-}"
    case "${target}" in
      gui/[0-9]*)
        remainder="${target#*/}"
        case "${remainder}" in
          */*)
            label="${target##*/}"
            if [[ -n "${TEST_LAUNCHCTL_STATE_FILE:-}" ]]; then
              if state_contains "${label}"; then
                printf 'state = waiting\n'
                exit 0
              fi
              exit 1
            fi
            case "|${TEST_LAUNCHCTL_LOADED_LABELS:-}|" in
              *"|${label}|"*) printf 'state = waiting\n'; exit 0 ;;
            esac
            if [[ -f "${HOME}/Library/LaunchAgents/${label}.plist" ]]; then
              printf 'state = waiting\n'
              exit 0
            fi
            exit 1
            ;;
          *) exit 0 ;;
        esac
        ;;
    esac
    ;;
  print-disabled)
    if [[ -n "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE:-}" ]]; then
      printf '%s\n' 'disabled services = {'
      while IFS='=' read -r label value; do
        [[ -n "${label}" ]] || continue
        printf '    "%s" => %s\n' "${label}" "${value}"
      done < "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE}"
      printf '%s\n' '}'
    else
      printf '%s\n' "${TEST_LAUNCHCTL_DISABLED_OUTPUT:-disabled services = {}}"
    fi
    exit 0
    ;;
  bootout)
    label="${2##*/}"
    if [[ -n "${TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE:-}" ]]; then
      printf 'call\n' >> "${TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE}"
      call_count="$(wc -l < "${TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE}" | tr -d ' ')"
      if [[ -n "${TEST_LAUNCHCTL_BOOTOUT_FAIL_N:-}" ]] \
        && [[ "${call_count}" == "${TEST_LAUNCHCTL_BOOTOUT_FAIL_N}" ]]; then
        exit 44
      fi
    fi
    if [[ -n "${TEST_LAUNCHCTL_BOOTOUT_FAIL_LABEL:-}" ]] \
      && [[ " $* " == *"${TEST_LAUNCHCTL_BOOTOUT_FAIL_LABEL}"* ]]; then
      exit 1
    fi
    state_remove "${label}"
    ;;
  bootstrap)
    plist_path="${3:-}"
    label="$(basename "${plist_path}" .plist)"
    if [[ -n "${TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE:-}" ]]; then
      printf 'call\n' >> "${TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE}"
      call_count="$(wc -l < "${TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE}" | tr -d ' ')"
      if [[ -n "${TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N:-}" ]] \
        && [[ "${call_count}" == "${TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N}" ]]; then
        if [[ "${TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL:-0}" == "1" ]]; then
          state_add "${label}"
        fi
        exit 43
      fi
    fi
    if [[ -n "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE:-}" ]] \
      && grep -Fxq -- "${label}=true" "${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE}"; then
      exit 45
    fi
    state_add "${label}"
    ;;
  enable)
    label="${2##*/}"
    override_set "${label}" false
    ;;
  disable)
    label="${2##*/}"
    if [[ -n "${TEST_LAUNCHCTL_DISABLE_FAIL_LABEL:-}" ]] \
      && [[ "${label}" == "${TEST_LAUNCHCTL_DISABLE_FAIL_LABEL}" ]]; then
      exit 46
    fi
    override_set "${label}" true
    ;;
esac
exit 0
STUB

  cat > "${FAKE_BIN}/gh" <<'STUB'
#!/usr/bin/env bash
printf 'gh' >> "${TEST_COMMAND_LOG}"
for arg in "$@"; do printf '\t%s' "${arg}" >> "${TEST_COMMAND_LOG}"; done
printf '\n' >> "${TEST_COMMAND_LOG}"
case "${1:-} ${2:-}" in
  "run list") printf '[]\n' ;;
esac
exit 0
STUB

  cat > "${FAKE_BIN}/jq" <<'STUB'
#!/usr/bin/env bash
cat >/dev/null
exit 0
STUB

  cat > "${FAKE_BIN}/codex" <<'STUB'
#!/usr/bin/env bash
printf 'codex' >> "${TEST_COMMAND_LOG}"
for arg in "$@"; do printf '\t%s' "${arg}" >> "${TEST_COMMAND_LOG}"; done
printf '\n' >> "${TEST_COMMAND_LOG}"
printf 'READY\n'
exit 0
STUB

  if ! command -v plutil >/dev/null 2>&1; then
    cat > "${FAKE_BIN}/plutil" <<'STUB'
#!/usr/bin/env bash
case "${1:-}" in
  -lint)
    grep -Fq '<plist version="1.0">' "${2:-}" || exit 1
    grep -Fq '<key>ProgramArguments</key>' "${2:-}" || exit 1
    ;;
  -extract)
    [[ "${2:-}" == "Label" && "${3:-}" == "raw" && "${4:-}" == "-o" ]] || exit 2
    sed -n 's|.*<key>Label</key><string>\([^<]*\)</string>.*|\1|p' "${6:-}" | head -n 1
    ;;
  *) exit 2 ;;
esac
STUB
  fi

  chmod 0755 "${FAKE_BIN}"/*
}

prepare_profile_portfolio() {
  local portfolio_root="$1"
  local tier="$2"
  local repo_names="traction-control clockwork"
  local repo_name

  case "${tier}" in
    moderate) repo_names="${repo_names} archility tachometer" ;;
    heavy) repo_names="${repo_names} archility tachometer auto-pass shock-relay" ;;
  esac

  mkdir -p "${portfolio_root}/util-repos"
  for repo_name in ${repo_names}; do
    mkdir -p "${portfolio_root}/util-repos/${repo_name}/.git"
  done
  if [[ "${tier}" != "light" ]]; then
    mkdir -p "${portfolio_root}/util-repos/archility/src/archility"
  fi
}

run_installer() {
  local case_root="$1"
  shift
  mkdir -p "${case_root}/home" "${case_root}/xdg-config" "${case_root}/xdg-data"
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${case_root}/home" \
    XDG_CONFIG_HOME="${case_root}/xdg-config" \
    XDG_DATA_HOME="${case_root}/xdg-data" \
    TEST_LAUNCHCTL_BOOTOUT_FAIL_LABEL="${TEST_LAUNCHCTL_BOOTOUT_FAIL_LABEL:-}" \
    TEST_LAUNCHCTL_BOOTOUT_FAIL_N="${TEST_LAUNCHCTL_BOOTOUT_FAIL_N:-}" \
    TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE="${TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE:-}" \
    TEST_LAUNCHCTL_LOADED_LABELS="${TEST_LAUNCHCTL_LOADED_LABELS:-}" \
    TEST_LAUNCHCTL_STATE_FILE="${TEST_LAUNCHCTL_STATE_FILE:-}" \
    TEST_LAUNCHCTL_OVERRIDE_STATE_FILE="${TEST_LAUNCHCTL_OVERRIDE_STATE_FILE:-}" \
    TEST_LAUNCHCTL_DISABLED_OUTPUT="${TEST_LAUNCHCTL_DISABLED_OUTPUT:-}" \
    TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N="${TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N:-}" \
    TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE="${TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE:-}" \
    TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL="${TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL:-}" \
    TEST_LAUNCHCTL_DISABLE_FAIL_LABEL="${TEST_LAUNCHCTL_DISABLE_FAIL_LABEL:-}" \
    TEST_CLOCKWORK_FAIL_N="${TEST_CLOCKWORK_FAIL_N:-}" \
    TEST_CLOCKWORK_CALL_COUNT_FILE="${TEST_CLOCKWORK_CALL_COUNT_FILE:-}" \
    TEST_CLOCKWORK_MISLABEL_JOB="${TEST_CLOCKWORK_MISLABEL_JOB:-}" \
    TEST_SYSTEMCTL_DISABLE_FAIL_UNIT="${TEST_SYSTEMCTL_DISABLE_FAIL_UNIT:-}" \
    /bin/bash "${INSTALLER}" "$@"
}

plist_names() {
  local plist_dir="$1"
  local output_path="$2"
  find "${plist_dir}" -type f -name 'io.github.casonk.traction-control.*.plist' -print \
    | sed 's|.*/||' \
    | sort > "${output_path}"
}

test_cli_and_dry_run() {
  local case_root="${TEST_ROOT}/dry run"
  local portfolio_root="${case_root}/portfolio"
  local dry_tier=""
  mkdir -p "${portfolio_root}"
  : > "${COMMAND_LOG}"

  run_installer "${case_root}" \
    --tier impossible \
    --portfolio-root "${portfolio_root}" \
    --platform macos \
    --dry-run
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "invalid tier is rejected"
  else
    fail_test "invalid tier is rejected" "installer unexpectedly succeeded"
  fi
  assert_output_contains "unsupported tier: impossible" "invalid tier reports the accepted boundary"

  : > "${COMMAND_LOG}"
  run_installer "${case_root}" \
    --tier light \
    --portfolio-root "${portfolio_root}" \
    --platform macos \
    --state-dir "${case_root}/state" \
    --launchd-dir "${case_root}/launch agents" \
    --dry-run
  assert_status 0 "light dry-run succeeds with every repo initially missing"
  assert_output_contains "dry-run complete; no files, repositories, or services were changed" "dry-run reports its no-write contract"
  if [[ ! -e "${case_root}/state" && ! -e "${case_root}/launch agents" ]] \
    && [[ -z "$(find "${portfolio_root}" -mindepth 1 -print -quit)" ]]; then
    pass "dry-run creates no state, scheduler, or repository files"
  else
    fail_test "dry-run creates no state, scheduler, or repository files"
  fi
  if [[ ! -s "${COMMAND_LOG}" ]]; then
    pass "dry-run invokes no network or scheduler command stubs"
  else
    fail_test "dry-run invokes no network or scheduler command stubs" "$(sed 's/^/    /' "${COMMAND_LOG}")"
  fi

  for dry_tier in moderate heavy; do
    : > "${COMMAND_LOG}"
    run_installer "${case_root}" \
      --tier "${dry_tier}" \
      --portfolio-root "${portfolio_root}" \
      --platform macos \
      --state-dir "${case_root}/state-${dry_tier}" \
      --launchd-dir "${case_root}/launch-agents-${dry_tier}" \
      --dry-run
    assert_status 0 "${dry_tier} dry-run succeeds before support repos are cloned"
    if [[ ! -e "${case_root}/state-${dry_tier}" && ! -e "${case_root}/launch-agents-${dry_tier}" ]]; then
      pass "${dry_tier} dry-run keeps its scheduler and shim plan non-mutating"
    else
      fail_test "${dry_tier} dry-run keeps its scheduler and shim plan non-mutating"
    fi
  done

  : > "${COMMAND_LOG}"
  run_installer "${case_root}" \
    --tier light \
    --portfolio-root "${portfolio_root}" \
    --platform macos \
    --launchd-dir "${case_root}/home/Library/LaunchAgents/"
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "render-only mode rejects a canonically spelled live LaunchAgents path"
  else
    fail_test "render-only mode rejects a canonically spelled live LaunchAgents path"
  fi
  assert_output_contains "refusing render-only output in the live LaunchAgents directory" "live-directory rejection explains the activation boundary"
  if ! grep -q '^git[[:space:]]clone[[:space:]]' "${COMMAND_LOG}" 2>/dev/null; then
    pass "live-directory rejection occurs before any repository clone"
  else
    fail_test "live-directory rejection occurs before any repository clone"
  fi
}

test_macos_tier() {
  local tier="$1"
  local expected_count="$2"
  local case_root="${TEST_ROOT}/macos ${tier}"
  local portfolio_root="${case_root}/portfolio root"
  local plist_dir="${case_root}/launch agents"
  local actual_names="${case_root}/actual.txt"
  local expected_names="${case_root}/expected.txt"
  local plist_path
  local lint_failed=0
  local launchd_manifest=""
  local clockwork_launchd_calls=0

  prepare_profile_portfolio "${portfolio_root}" "${tier}"
  mkdir -p "${plist_dir}"
  printf 'preserve me\n' > "${plist_dir}/unrelated.plist"
  : > "${COMMAND_LOG}"

  run_installer "${case_root}" \
    --tier "${tier}" \
    --portfolio-root "${portfolio_root}" \
    --platform macos \
    --provider codex \
    --model 'gpt test & safe' \
    --no-clone \
    --state-dir "${case_root}/state" \
    --launchd-dir "${plist_dir}"
  assert_status 0 "macOS ${tier} profile renders successfully"
  assert_output_contains "(${expected_count} jobs)" "macOS ${tier} reports the expected job count"

  plist_names "${plist_dir}" "${actual_names}"
  case "${tier}" in
    light)
      cat > "${expected_names}" <<'EOF'
io.github.casonk.traction-control.bug-sweep-agentic.plist
io.github.casonk.traction-control.ci-repair-agentic-discovery.plist
io.github.casonk.traction-control.portfolio-audit-daily.plist
EOF
      ;;
    moderate)
      cat > "${expected_names}" <<'EOF'
io.github.casonk.traction-control.archility-daily.plist
io.github.casonk.traction-control.archility-weekly.plist
io.github.casonk.traction-control.bug-sweep-agentic.plist
io.github.casonk.traction-control.ci-repair-agentic-discovery.plist
io.github.casonk.traction-control.portfolio-audit-daily.plist
io.github.casonk.traction-control.refs-audit-agentic.plist
io.github.casonk.traction-control.template-consolidation-agentic.plist
EOF
      ;;
    heavy)
      cat > "${expected_names}" <<'EOF'
io.github.casonk.traction-control.archility-daily.plist
io.github.casonk.traction-control.archility-weekly.plist
io.github.casonk.traction-control.bug-sweep-agentic.plist
io.github.casonk.traction-control.ci-repair-agentic-discovery.plist
io.github.casonk.traction-control.ci-repair-agentic-repair.plist
io.github.casonk.traction-control.portfolio-audit-daily.plist
io.github.casonk.traction-control.refs-audit-agentic.plist
io.github.casonk.traction-control.tachometer-disk-pressure-agentic.plist
io.github.casonk.traction-control.template-consolidation-agentic.plist
EOF
      ;;
  esac
  assert_line_sets_equal "${actual_names}" "${expected_names}" "macOS ${tier} renders the exact cumulative job mapping"

  if command -v plutil >/dev/null 2>&1; then
    while IFS= read -r plist_path; do
      plutil -lint "${plist_path}" >/dev/null 2>&1 || lint_failed=1
    done < <(find "${plist_dir}" -type f -name 'io.github.casonk.traction-control.*.plist' -print)
    if (( lint_failed == 0 )); then
      pass "macOS ${tier} plists pass plutil lint"
    else
      fail_test "macOS ${tier} plists pass plutil lint"
    fi
  fi

  assert_file_contains \
    "${plist_dir}/io.github.casonk.traction-control.bug-sweep-agentic.plist" \
    '<key>ProgramArguments</key>' \
    "macOS ${tier} uses a ProgramArguments array"
  assert_file_contains \
    "${plist_dir}/io.github.casonk.traction-control.bug-sweep-agentic.plist" \
    '<integer>300</integer>' \
    "macOS ${tier} uses the bounded stateful interval poll"
  assert_file_contains \
    "${plist_dir}/io.github.casonk.traction-control.bug-sweep-agentic.plist" \
    'gpt test &amp; safe' \
    "macOS ${tier} XML-escapes the model value"
  if [[ "${tier}" == "light" ]]; then
    launchd_manifest="${case_root}/state/rendered/clockwork-launchd/bug-sweep-agentic.toml"
    assert_file_contains \
      "${launchd_manifest}" \
      'launchd_label = "io.github.casonk.traction-control.bug-sweep-agentic"' \
      "macOS tier manifest gives Clockwork the authoritative downstream label"
    assert_file_contains \
      "${launchd_manifest}" '--startup-delay-seconds' \
      "macOS tier manifest keeps boot-relative delayed-start handling explicit"
    assert_file_contains \
      "${launchd_manifest}" '--interval-seconds' \
      "macOS tier manifest gives the stateful adapter the original interval"
    assert_file_contains \
      "${launchd_manifest}" '--retry-seconds' \
      "macOS tier manifest bounds failed due retries to the poll cadence"
    assert_file_contains \
      "${launchd_manifest}" '--jitter-seconds' \
      "macOS tier manifest keeps randomized delay explicit in the adapter"
    assert_file_contains \
      "${launchd_manifest}" '--network-host' \
      "macOS tier manifest keeps network ordering explicit in the adapter"
    assert_file_contains \
      "${launchd_manifest}" 'launchd_run_at_load = true' \
      "macOS interval poll explicitly requests a login-time due check"
    assert_file_contains \
      "${launchd_manifest}" 'on_unit_active_sec = "300s"' \
      "macOS interval adapter truthfully renders its five-minute poll"
    assert_file_contains \
      "${launchd_manifest}" 'environment_files = ["-' \
      "macOS tier manifest delegates private environment loading to Clockwork"
    assert_file_not_contains \
      "${launchd_manifest}" '--env-file' \
      "macOS tier manifest never asks bash to parse a private environment file"
    assert_file_not_contains \
      "${launchd_manifest}" 'on_boot_sec' \
      "macOS adapter does not claim Clockwork mapped systemd OnBootSec"
    assert_file_not_contains \
      "${launchd_manifest}" 'randomized_delay_sec' \
      "macOS adapter does not claim Clockwork mapped systemd randomized delay"
    assert_file_not_contains \
      "${launchd_manifest}" 'after =' \
      "macOS adapter does not claim launchd has systemd ordering dependencies"
  fi
  assert_file_contains "${plist_dir}/unrelated.plist" 'preserve me' "macOS ${tier} preserves unrelated LaunchAgents"
  clockwork_launchd_calls="$(grep -c $'^clockwork\tinstall\t.*\t--target\tlaunchd-user\t' "${COMMAND_LOG}" || true)"
  if [[ "${clockwork_launchd_calls}" == "${expected_count}" ]]; then
    pass "macOS ${tier} delegates every selected plist render to Clockwork"
  else
    fail_test "macOS ${tier} delegates every selected plist render to Clockwork" \
      "recorded ${clockwork_launchd_calls} Clockwork launchd renders"
  fi
  assert_no_logged_command launchctl "macOS ${tier} render-only mode never calls launchctl"
}

test_autonomous_heavy_render() {
  local case_root="${TEST_ROOT}/macos autonomous heavy"
  local portfolio_root="${case_root}/portfolio root"
  local plist_dir="${case_root}/launch agents"
  local plist_count

  prepare_profile_portfolio "${portfolio_root}" heavy
  : > "${COMMAND_LOG}"
  run_installer "${case_root}" \
    --tier heavy \
    --enable-autonomous-ci-repair \
    --portfolio-root "${portfolio_root}" \
    --platform macos \
    --provider codex \
    --no-clone \
    --state-dir "${case_root}/state" \
    --launchd-dir "${plist_dir}"
  assert_status 0 "autonomous heavy macOS profile renders successfully"
  assert_file_exists \
    "${plist_dir}/io.github.casonk.traction-control.ci-repair-agentic.plist" \
    "autonomous heavy includes the scheduled full CI repair job"
  if [[ ! -e "${plist_dir}/io.github.casonk.traction-control.ci-repair-agentic-discovery.plist" ]]; then
    pass "autonomous heavy replaces read-only CI discovery in the selected set"
  else
    fail_test "autonomous heavy replaces read-only CI discovery in the selected set"
  fi
  plist_count="$(find "${plist_dir}" -type f -name 'io.github.casonk.traction-control.*.plist' | wc -l | tr -d ' ')"
  if [[ "${plist_count}" == "9" ]]; then
    pass "autonomous heavy keeps the expected nine-job profile size"
  else
    fail_test "autonomous heavy keeps the expected nine-job profile size" "found ${plist_count} plists"
  fi
}

test_profile_reconciliation_render() {
  local case_root="${TEST_ROOT}/profile reconciliation"
  local portfolio_root="${case_root}/portfolio"
  local plist_dir="${case_root}/launch agents"
  local state_dir="${case_root}/state"
  local managed_count=""
  local unit_dir="${case_root}/systemd units"
  local unit_count=""

  prepare_profile_portfolio "${portfolio_root}" heavy
  mkdir -p "${plist_dir}"
  printf 'preserve me\n' > "${plist_dir}/unrelated.plist"

  run_installer "${case_root}" \
    --tier heavy --portfolio-root "${portfolio_root}" --platform macos \
    --provider codex --no-clone --state-dir "${state_dir}" --launchd-dir "${plist_dir}"
  assert_status 0 "normal heavy render prepares the reconciliation fixture"
  run_installer "${case_root}" \
    --tier heavy --enable-autonomous-ci-repair \
    --portfolio-root "${portfolio_root}" --platform macos \
    --provider codex --no-clone --state-dir "${state_dir}" --launchd-dir "${plist_dir}"
  assert_status 0 "normal-to-autonomous heavy render reconciles successfully"
  if [[ -f "${plist_dir}/io.github.casonk.traction-control.ci-repair-agentic.plist" ]] \
    && [[ ! -e "${plist_dir}/io.github.casonk.traction-control.ci-repair-agentic-discovery.plist" ]]; then
    pass "autonomous render archives the unselected discovery plist"
  else
    fail_test "autonomous render archives the unselected discovery plist"
  fi

  run_installer "${case_root}" \
    --tier heavy --portfolio-root "${portfolio_root}" --platform macos \
    --provider codex --no-clone --state-dir "${state_dir}" --launchd-dir "${plist_dir}"
  assert_status 0 "autonomous-to-normal heavy render reconciles successfully"
  if [[ -f "${plist_dir}/io.github.casonk.traction-control.ci-repair-agentic-discovery.plist" ]] \
    && [[ ! -e "${plist_dir}/io.github.casonk.traction-control.ci-repair-agentic.plist" ]]; then
    pass "normal heavy render archives the unselected autonomous plist"
  else
    fail_test "normal heavy render archives the unselected autonomous plist"
  fi

  run_installer "${case_root}" \
    --tier light --portfolio-root "${portfolio_root}" --platform macos \
    --provider codex --no-clone --state-dir "${state_dir}" --launchd-dir "${plist_dir}"
  assert_status 0 "heavy-to-light macOS render reconciles successfully"
  managed_count="$(find "${plist_dir}" -type f -name 'io.github.casonk.traction-control.*.plist' | wc -l | tr -d ' ')"
  if [[ "${managed_count}" == "3" ]] \
    && [[ -f "${plist_dir}/io.github.casonk.traction-control.portfolio-audit-daily.plist" ]]; then
    pass "heavy-to-light macOS render leaves exactly the light managed artifacts"
  else
    fail_test "heavy-to-light macOS render leaves exactly the light managed artifacts" "found ${managed_count} managed plists"
  fi
  assert_file_contains "${plist_dir}/unrelated.plist" 'preserve me' "macOS profile reconciliation preserves unrelated plists"

  mkdir -p "${unit_dir}"
  printf 'preserve me\n' > "${unit_dir}/unrelated.service"
  run_installer "${case_root}" \
    --tier heavy --portfolio-root "${portfolio_root}" --platform linux \
    --provider codex --no-clone --state-dir "${state_dir}" --systemd-unit-dir "${unit_dir}"
  assert_status 0 "heavy Linux render prepares the downgrade fixture"
  run_installer "${case_root}" \
    --tier light --portfolio-root "${portfolio_root}" --platform linux \
    --provider codex --no-clone --state-dir "${state_dir}" --systemd-unit-dir "${unit_dir}"
  assert_status 0 "heavy-to-light Linux render reconciles successfully"
  unit_count="$(find "${unit_dir}" -type f \( -name 'portfolio-audit-daily.*' -o -name 'bug-sweep-agentic.*' -o -name 'ci-repair-agentic-discovery.*' \) | wc -l | tr -d ' ')"
  if [[ "${unit_count}" == "6" ]] \
    && [[ ! -e "${unit_dir}/tachometer-disk-pressure-agentic.timer" ]]; then
    pass "heavy-to-light Linux render leaves exactly the light managed artifacts"
  else
    fail_test "heavy-to-light Linux render leaves exactly the light managed artifacts" "found ${unit_count} light unit files"
  fi
  assert_file_contains "${unit_dir}/unrelated.service" 'preserve me' "Linux profile reconciliation preserves unrelated units"
}

test_repository_safety() {
  local https_case="${TEST_ROOT}/clone https"
  local https_portfolio="${https_case}/portfolio"
  local ssh_case="${TEST_ROOT}/clone ssh"
  local ssh_portfolio="${ssh_case}/portfolio"
  local mismatch_case="${TEST_ROOT}/origin mismatch"
  local mismatch_portfolio="${mismatch_case}/portfolio"
  local mismatch_config="${mismatch_case}/repos.conf"
  local collision_case="${TEST_ROOT}/non-git collision"
  local collision_portfolio="${collision_case}/portfolio"
  local nested_case="${TEST_ROOT}/nested checkout"
  local nested_portfolio="${nested_case}/portfolio"
  local nested_config="${nested_case}/repos.conf"

  mkdir -p "${https_portfolio}"
  : > "${COMMAND_LOG}"
  run_installer "${https_case}" \
    --tier light --portfolio-root "${https_portfolio}" --platform macos --no-scheduler
  assert_status 0 "HTTPS support-repo cloning succeeds through the git stub"
  assert_file_exists "${https_portfolio}/util-repos/traction-control/.git/HEAD" "HTTPS clone creates the traction-control checkout target"
  if grep -Fq $'git\tclone\t--origin\torigin\thttps://github.com/casonk/traction-control.git' "${COMMAND_LOG}" \
    && grep -Fq $'git\tclone\t--origin\torigin\thttps://github.com/casonk/clockwork.git' "${COMMAND_LOG}"; then
    pass "HTTPS clone argv uses the exact two allowlisted light remotes"
  else
    fail_test "HTTPS clone argv uses the exact two allowlisted light remotes" "$(sed 's/^/    /' "${COMMAND_LOG}")"
  fi

  mkdir -p "${ssh_portfolio}"
  : > "${COMMAND_LOG}"
  run_installer "${ssh_case}" \
    --tier light --portfolio-root "${ssh_portfolio}" --platform macos \
    --clone-protocol ssh --no-scheduler
  assert_status 0 "SSH support-repo cloning succeeds through the git stub"
  if grep -Fq $'git\tclone\t--origin\torigin\tgit@github.com:casonk/traction-control.git' "${COMMAND_LOG}"; then
    pass "SSH clone argv uses the expected allowlisted remote"
  else
    fail_test "SSH clone argv uses the expected allowlisted remote"
  fi

  mkdir -p "${mismatch_portfolio}/util-repos/actual/.git" "${mismatch_case}"
  cat > "${mismatch_config}" <<'EOF'
light|actual|casonk/expected|util-repos/actual|origin mismatch fixture
EOF
  run_installer "${mismatch_case}" \
    --tier light --portfolio-root "${mismatch_portfolio}" --platform macos \
    --repo-config "${mismatch_config}" --no-clone --no-scheduler
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "an existing checkout with the wrong origin is rejected"
  else
    fail_test "an existing checkout with the wrong origin is rejected"
  fi
  assert_output_contains "origin mismatch" "origin mismatch reports the failed invariant"

  mkdir -p "${collision_portfolio}/util-repos/traction-control"
  run_installer "${collision_case}" \
    --tier light --portfolio-root "${collision_portfolio}" --platform macos \
    --no-clone --no-scheduler
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "a non-git checkout collision is rejected"
  else
    fail_test "a non-git checkout collision is rejected"
  fi

  mkdir -p "${nested_portfolio}/util-repos/nested/.git" "${nested_portfolio}/parent" "${nested_case}"
  printf '%s\n' "${nested_portfolio}/parent" > "${nested_portfolio}/util-repos/nested/.fake-git-top"
  cat > "${nested_config}" <<'EOF'
light|nested|casonk/nested|util-repos/nested|nested checkout fixture
EOF
  run_installer "${nested_case}" \
    --tier light --portfolio-root "${nested_portfolio}" --platform macos \
    --repo-config "${nested_config}" --no-clone --no-scheduler
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "a target that is only a nested checkout directory is rejected"
  else
    fail_test "a target that is only a nested checkout directory is rejected"
  fi
  assert_output_contains "only a subdirectory" "nested checkout rejection explains the exact-root requirement"
}

test_activation_with_stubs() {
  local mac_case="${TEST_ROOT}/macos activation"
  local mac_portfolio="${mac_case}/portfolio"
  local mac_launchd="${mac_case}/home/Library/LaunchAgents"
  local bootstrap_count
  local first_bootout_line=""
  local last_render_line=""
  local linux_case="${TEST_ROOT}/linux activation"
  local linux_portfolio="${linux_case}/portfolio"
  local linux_units="${linux_case}/xdg-config/systemd/user"
  local enable_count

  prepare_profile_portfolio "${mac_portfolio}" light
  mkdir -p "${mac_launchd}"
  printf 'stale heavy plist\n' \
    > "${mac_launchd}/io.github.casonk.traction-control.tachometer-disk-pressure-agentic.plist"
  : > "${COMMAND_LOG}"
  run_installer "${mac_case}" \
    --tier light \
    --portfolio-root "${mac_portfolio}" \
    --platform macos \
    --provider codex \
    --no-clone \
    --activate \
    --state-dir "${mac_case}/state" \
    --launchd-dir "${mac_launchd}"
  assert_status 0 "macOS light activation succeeds against the launchctl stub"
  bootstrap_count="$(grep -c '^launchctl[[:space:]]bootstrap[[:space:]]' "${COMMAND_LOG}" 2>/dev/null || true)"
  if [[ "${bootstrap_count}" == "3" ]]; then
    pass "macOS light activation bootstraps exactly three selected LaunchAgents"
  else
    fail_test "macOS light activation bootstraps exactly three selected LaunchAgents" "recorded ${bootstrap_count} bootstrap calls"
  fi
  if ! grep -Eq '^launchctl[[:space:]](enable|disable)[[:space:]]' "${COMMAND_LOG}"; then
    pass "macOS activation preserves absent disabled-override entries"
  else
    fail_test "macOS activation preserves absent disabled-override entries"
  fi
  first_bootout_line="$(grep -n '^launchctl[[:space:]]bootout[[:space:]]' "${COMMAND_LOG}" | sed -n '1s/:.*//p')"
  last_render_line="$(grep -n $'^clockwork\tinstall\t.*\t--target\tlaunchd-user\t' "${COMMAND_LOG}" | sed -n '$s/:.*//p')"
  if [[ -n "${first_bootout_line}" && -n "${last_render_line}" ]] \
    && (( last_render_line < first_bootout_line )); then
    pass "macOS activation pre-renders every selected candidate before unloading"
  else
    fail_test "macOS activation pre-renders every selected candidate before unloading"
  fi
  if grep -Fq 'io.github.casonk.traction-control.tachometer-disk-pressure-agentic' "${COMMAND_LOG}" \
    && grep -q '^launchctl[[:space:]]bootout[[:space:]]' "${COMMAND_LOG}"; then
    pass "macOS light activation unloads known unselected heavy jobs"
  else
    fail_test "macOS light activation unloads known unselected heavy jobs"
  fi

  prepare_profile_portfolio "${linux_portfolio}" light
  : > "${COMMAND_LOG}"
  run_installer "${linux_case}" \
    --tier light \
    --portfolio-root "${linux_portfolio}" \
    --platform linux \
    --provider codex \
    --no-clone \
    --activate \
    --state-dir "${linux_case}/state" \
    --systemd-unit-dir "${linux_units}"
  assert_status 0 "Linux light activation succeeds against the systemctl stub"
  enable_count="$(grep -c '^systemctl[[:space:]]--user[[:space:]]enable[[:space:]]--now[[:space:]]' "${COMMAND_LOG}" 2>/dev/null || true)"
  if [[ "${enable_count}" == "3" ]]; then
    pass "Linux light activation enables exactly three selected timers"
  else
    fail_test "Linux light activation enables exactly three selected timers" "recorded ${enable_count} enable calls"
  fi
  if grep -Fq $'systemctl\t--user\tdisable\t--now\ttachometer-disk-pressure-agentic.timer' "${COMMAND_LOG}"; then
    pass "Linux light activation disables known unselected heavy timers"
  else
    fail_test "Linux light activation disables known unselected heavy timers"
  fi
}

test_reconciliation_fail_closed() {
  local mac_case="${TEST_ROOT}/macos reconciliation failure"
  local mac_portfolio="${mac_case}/portfolio"
  local linux_case="${TEST_ROOT}/linux reconciliation failure"
  local linux_portfolio="${linux_case}/portfolio"

  prepare_profile_portfolio "${mac_portfolio}" light
  mkdir -p "${mac_case}/home/Library/LaunchAgents"
  printf 'stale heavy plist\n' \
    > "${mac_case}/home/Library/LaunchAgents/io.github.casonk.traction-control.tachometer-disk-pressure-agentic.plist"
  : > "${COMMAND_LOG}"
  TEST_LAUNCHCTL_BOOTOUT_FAIL_LABEL='io.github.casonk.traction-control.tachometer-disk-pressure-agentic'
  run_installer "${mac_case}" \
    --tier light --portfolio-root "${mac_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${mac_case}/state"
  TEST_LAUNCHCTL_BOOTOUT_FAIL_LABEL=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation fails closed when an unselected job cannot be unloaded"
  else
    fail_test "macOS activation fails closed when an unselected job cannot be unloaded"
  fi
  if ! grep -Eq '^launchctl[[:space:]]bootstrap[[:space:]].*io\.github\.casonk\.traction-control\.(portfolio-audit-daily|bug-sweep-agentic|ci-repair-agentic-discovery)\.plist' \
    "${COMMAND_LOG}" 2>/dev/null; then
    pass "macOS reconciliation failure occurs before selected candidates are bootstrapped"
  else
    fail_test "macOS reconciliation failure occurs before selected candidates are bootstrapped"
  fi

  prepare_profile_portfolio "${linux_portfolio}" light
  : > "${COMMAND_LOG}"
  TEST_SYSTEMCTL_DISABLE_FAIL_UNIT='tachometer-disk-pressure-agentic.timer'
  run_installer "${linux_case}" \
    --tier light --portfolio-root "${linux_portfolio}" --platform linux \
    --provider codex --no-clone --activate --state-dir "${linux_case}/state"
  TEST_SYSTEMCTL_DISABLE_FAIL_UNIT=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "Linux activation fails closed when an unselected timer cannot be disabled"
  else
    fail_test "Linux activation fails closed when an unselected timer cannot be disabled"
  fi
  if ! grep -q '^systemctl[[:space:]]--user[[:space:]]enable[[:space:]]--now[[:space:]]' "${COMMAND_LOG}" 2>/dev/null; then
    pass "Linux reconciliation failure occurs before selected timers are enabled"
  else
    fail_test "Linux reconciliation failure occurs before selected timers are enabled"
  fi
}

test_launchd_transaction_failures() {
  local render_case="${TEST_ROOT}/macos nth render failure"
  local render_portfolio="${render_case}/portfolio"
  local render_launchd="${render_case}/home/Library/LaunchAgents"
  local render_count_file="${render_case}/clockwork-calls"
  local bootstrap_case="${TEST_ROOT}/macos nth bootstrap failure"
  local bootstrap_portfolio="${bootstrap_case}/portfolio"
  local bootstrap_launchd="${bootstrap_case}/home/Library/LaunchAgents"
  local bootstrap_count_file="${bootstrap_case}/bootstrap-calls"
  local bootstrap_state_file="${bootstrap_case}/loaded-labels"
  local bootstrap_expected_state="${bootstrap_case}/expected-loaded-labels"
  local mislabel_case="${TEST_ROOT}/macos candidate label mismatch"
  local mislabel_portfolio="${mislabel_case}/portfolio"
  local bootout_case="${TEST_ROOT}/macos nth bootout failure"
  local bootout_portfolio="${bootout_case}/portfolio"
  local bootout_launchd="${bootout_case}/home/Library/LaunchAgents"
  local bootout_count_file="${bootout_case}/bootout-calls"
  local bootout_state_file="${bootout_case}/loaded-labels"
  local bootout_expected_state="${bootout_case}/expected-loaded-labels"
  local override_case="${TEST_ROOT}/macos exact override rollback"
  local override_portfolio="${override_case}/portfolio"
  local override_launchd="${override_case}/home/Library/LaunchAgents"
  local override_count_file="${override_case}/bootstrap-calls"
  local override_loaded_file="${override_case}/loaded-labels"
  local override_state_file="${override_case}/disabled-overrides"
  local override_expected_state="${override_case}/expected-disabled-overrides"
  local malformed_case="${TEST_ROOT}/macos malformed override snapshot"
  local malformed_portfolio="${malformed_case}/portfolio"
  local incomplete_case="${TEST_ROOT}/macos unverifiable override rollback"
  local incomplete_portfolio="${incomplete_case}/portfolio"
  local incomplete_launchd="${incomplete_case}/home/Library/LaunchAgents"
  local incomplete_count_file="${incomplete_case}/bootstrap-calls"
  local incomplete_loaded_file="${incomplete_case}/loaded-labels"
  local incomplete_state_file="${incomplete_case}/disabled-overrides"
  local label=""

  prepare_profile_portfolio "${render_portfolio}" light
  mkdir -p "${render_launchd}"
  printf 'preserve before render failure\n' \
    > "${render_launchd}/io.github.casonk.traction-control.tachometer-disk-pressure-agentic.plist"
  : > "${render_count_file}"
  : > "${COMMAND_LOG}"
  TEST_CLOCKWORK_FAIL_N=2
  TEST_CLOCKWORK_CALL_COUNT_FILE="${render_count_file}"
  run_installer "${render_case}" \
    --tier light --portfolio-root "${render_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${render_case}/state"
  TEST_CLOCKWORK_FAIL_N=''
  TEST_CLOCKWORK_CALL_COUNT_FILE=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation fails when the second candidate render fails"
  else
    fail_test "macOS activation fails when the second candidate render fails"
  fi
  assert_file_contains \
    "${render_launchd}/io.github.casonk.traction-control.tachometer-disk-pressure-agentic.plist" \
    'preserve before render failure' \
    "nth-render failure preserves the prior unselected LaunchAgent"
  if ! grep -Eq '^launchctl[[:space:]](bootout|enable|disable|bootstrap)[[:space:]]' "${COMMAND_LOG}"; then
    pass "nth-render failure performs no launchd transition"
  else
    fail_test "nth-render failure performs no launchd transition"
  fi

  prepare_profile_portfolio "${mislabel_portfolio}" light
  : > "${COMMAND_LOG}"
  TEST_CLOCKWORK_MISLABEL_JOB='bug-sweep-agentic'
  run_installer "${mislabel_case}" \
    --tier light --portfolio-root "${mislabel_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${mislabel_case}/state"
  TEST_CLOCKWORK_MISLABEL_JOB=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation rejects a candidate with a mismatched embedded Label"
  else
    fail_test "macOS activation rejects a candidate with a mismatched embedded Label"
  fi
  assert_output_contains 'pre-rendered LaunchAgent Label mismatch' \
    "candidate mismatch reports the exact label invariant"
  if ! grep -Eq '^launchctl[[:space:]](bootout|enable|disable|bootstrap)[[:space:]]' "${COMMAND_LOG}"; then
    pass "candidate Label mismatch performs no launchd transition"
  else
    fail_test "candidate Label mismatch performs no launchd transition"
  fi

  prepare_profile_portfolio "${bootstrap_portfolio}" light
  mkdir -p "${bootstrap_launchd}"
  for label in \
    portfolio-audit-daily \
    bug-sweep-agentic \
    ci-repair-agentic-discovery \
    tachometer-disk-pressure-agentic; do
    printf 'original %s\n' "${label}" \
      > "${bootstrap_launchd}/io.github.casonk.traction-control.${label}.plist"
  done
  printf '%s\n' \
    io.github.casonk.traction-control.portfolio-audit-daily \
    io.github.casonk.traction-control.bug-sweep-agentic \
    io.github.casonk.traction-control.ci-repair-agentic-discovery \
    io.github.casonk.traction-control.tachometer-disk-pressure-agentic \
    > "${bootstrap_state_file}"
  sort "${bootstrap_state_file}" > "${bootstrap_expected_state}"
  : > "${bootstrap_count_file}"
  : > "${COMMAND_LOG}"
  TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N=2
  TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE="${bootstrap_count_file}"
  TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL=1
  TEST_LAUNCHCTL_STATE_FILE="${bootstrap_state_file}"
  run_installer "${bootstrap_case}" \
    --tier light --portfolio-root "${bootstrap_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${bootstrap_case}/state"
  TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N=''
  TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE=''
  TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL=''
  TEST_LAUNCHCTL_STATE_FILE=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation fails when the second bootstrap fails"
  else
    fail_test "macOS activation fails when the second bootstrap fails"
  fi
  for label in \
    portfolio-audit-daily \
    bug-sweep-agentic \
    ci-repair-agentic-discovery \
    tachometer-disk-pressure-agentic; do
    assert_file_contains \
      "${bootstrap_launchd}/io.github.casonk.traction-control.${label}.plist" \
      "original ${label}" \
      "nth-bootstrap rollback restores ${label}"
  done
  if grep -Fq $'launchctl\tbootstrap\tgui/' "${COMMAND_LOG}" \
    && grep -Fq $'launchctl\tbootout\tgui/' "${COMMAND_LOG}"; then
    pass "nth-bootstrap failure attempts scheduler rollback"
  else
    fail_test "nth-bootstrap failure attempts scheduler rollback"
  fi
  sort "${bootstrap_state_file}" > "${bootstrap_state_file}.sorted"
  if cmp -s "${bootstrap_expected_state}" "${bootstrap_state_file}.sorted"; then
    pass "nth-bootstrap rollback restores the exact original loaded-label set"
  else
    fail_test "nth-bootstrap rollback restores the exact original loaded-label set"
  fi

  prepare_profile_portfolio "${bootout_portfolio}" light
  mkdir -p "${bootout_launchd}"
  for label in portfolio-audit-daily bug-sweep-agentic; do
    printf 'original %s\n' "${label}" \
      > "${bootout_launchd}/io.github.casonk.traction-control.${label}.plist"
    printf 'io.github.casonk.traction-control.%s\n' "${label}" \
      >> "${bootout_state_file}"
  done
  sort "${bootout_state_file}" > "${bootout_expected_state}"
  : > "${bootout_count_file}"
  : > "${COMMAND_LOG}"
  TEST_LAUNCHCTL_BOOTOUT_FAIL_N=2
  TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE="${bootout_count_file}"
  TEST_LAUNCHCTL_STATE_FILE="${bootout_state_file}"
  run_installer "${bootout_case}" \
    --tier light --portfolio-root "${bootout_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${bootout_case}/state"
  TEST_LAUNCHCTL_BOOTOUT_FAIL_N=''
  TEST_LAUNCHCTL_BOOTOUT_COUNT_FILE=''
  TEST_LAUNCHCTL_STATE_FILE=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation fails when the second managed bootout fails"
  else
    fail_test "macOS activation fails when the second managed bootout fails"
  fi
  sort "${bootout_state_file}" > "${bootout_state_file}.sorted"
  if cmp -s "${bootout_expected_state}" "${bootout_state_file}.sorted"; then
    pass "nth-bootout rollback restores the exact original loaded-label set"
  else
    fail_test "nth-bootout rollback restores the exact original loaded-label set"
  fi
  if [[ "$(grep -c '^launchctl[[:space:]]bootstrap[[:space:]]' "${COMMAND_LOG}" || true)" == "1" ]]; then
    pass "nth-bootout rollback reloads only the label already removed"
  else
    fail_test "nth-bootout rollback reloads only the label already removed"
  fi

  prepare_profile_portfolio "${override_portfolio}" light
  mkdir -p "${override_launchd}"
  for label in portfolio-audit-daily bug-sweep-agentic ci-repair-agentic-discovery; do
    printf 'original %s\n' "${label}" \
      > "${override_launchd}/io.github.casonk.traction-control.${label}.plist"
    printf 'io.github.casonk.traction-control.%s\n' "${label}" \
      >> "${override_loaded_file}"
  done
  printf '%s\n' \
    'io.github.casonk.traction-control.bug-sweep-agentic=false' \
    'io.github.casonk.traction-control.ci-repair-agentic-discovery=true' \
    > "${override_state_file}"
  sort "${override_state_file}" > "${override_expected_state}"
  : > "${override_count_file}"
  : > "${COMMAND_LOG}"
  TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N=3
  TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE="${override_count_file}"
  TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL=1
  TEST_LAUNCHCTL_STATE_FILE="${override_loaded_file}"
  TEST_LAUNCHCTL_OVERRIDE_STATE_FILE="${override_state_file}"
  run_installer "${override_case}" \
    --tier light --portfolio-root "${override_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${override_case}/state"
  TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N=''
  TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE=''
  TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL=''
  TEST_LAUNCHCTL_STATE_FILE=''
  TEST_LAUNCHCTL_OVERRIDE_STATE_FILE=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation fails after mutating an explicit disabled override"
  else
    fail_test "macOS activation fails after mutating an explicit disabled override"
  fi
  sort "${override_state_file}" > "${override_state_file}.sorted"
  if cmp -s "${override_expected_state}" "${override_state_file}.sorted"; then
    pass "launchd rollback restores absent, explicit enabled, and explicit disabled overrides exactly"
  else
    fail_test "launchd rollback restores absent, explicit enabled, and explicit disabled overrides exactly"
  fi
  if [[ "$(grep -c '^launchctl[[:space:]]enable[[:space:]]' "${COMMAND_LOG}" || true)" == "2" ]] \
    && [[ "$(grep -c '^launchctl[[:space:]]disable[[:space:]]' "${COMMAND_LOG}" || true)" == "1" ]] \
    && [[ "$(grep -Ec '^launchctl[[:space:]]enable[[:space:]]gui/[0-9]+/io\.github\.casonk\.traction-control\.ci-repair-agentic-discovery$' "${COMMAND_LOG}" || true)" == "2" ]] \
    && [[ "$(grep -Ec '^launchctl[[:space:]]disable[[:space:]]gui/[0-9]+/io\.github\.casonk\.traction-control\.ci-repair-agentic-discovery$' "${COMMAND_LOG}" || true)" == "1" ]]; then
    pass "activation mutates and rolls back only the originally disabled override"
  else
    fail_test "activation mutates and rolls back only the originally disabled override"
  fi

  prepare_profile_portfolio "${malformed_portfolio}" light
  : > "${COMMAND_LOG}"
  TEST_LAUNCHCTL_DISABLED_OUTPUT=$'disabled services = {\n    "io.github.casonk.traction-control.portfolio-audit-daily" => unknown\n}'
  run_installer "${malformed_case}" \
    --tier light --portfolio-root "${malformed_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${malformed_case}/state"
  TEST_LAUNCHCTL_DISABLED_OUTPUT=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "macOS activation rejects an ambiguous disabled-override snapshot"
  else
    fail_test "macOS activation rejects an ambiguous disabled-override snapshot"
  fi
  assert_output_contains 'cannot safely parse launchd disabled override' \
    "ambiguous disabled-override failure explains the invariant"
  if ! grep -Eq '^launchctl[[:space:]](bootout|enable|disable|bootstrap)[[:space:]]' "${COMMAND_LOG}"; then
    pass "ambiguous disabled-override snapshot fails before launchd mutation"
  else
    fail_test "ambiguous disabled-override snapshot fails before launchd mutation"
  fi

  # A rollback that cannot put every mutated override back must never look like a
  # clean rollback. Fail the restoring `launchctl disable` so the override map is
  # left differing from the pre-transaction snapshot, and require the installer to
  # report that difference explicitly instead of exiting quietly.
  prepare_profile_portfolio "${incomplete_portfolio}" light
  mkdir -p "${incomplete_launchd}"
  : > "${incomplete_loaded_file}"
  for label in portfolio-audit-daily bug-sweep-agentic ci-repair-agentic-discovery; do
    printf 'original %s\n' "${label}" \
      > "${incomplete_launchd}/io.github.casonk.traction-control.${label}.plist"
    printf 'io.github.casonk.traction-control.%s\n' "${label}" \
      >> "${incomplete_loaded_file}"
  done
  printf '%s\n' \
    'io.github.casonk.traction-control.bug-sweep-agentic=false' \
    'io.github.casonk.traction-control.ci-repair-agentic-discovery=true' \
    > "${incomplete_state_file}"
  : > "${incomplete_count_file}"
  : > "${COMMAND_LOG}"
  TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N=3
  TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE="${incomplete_count_file}"
  TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL=1
  TEST_LAUNCHCTL_STATE_FILE="${incomplete_loaded_file}"
  TEST_LAUNCHCTL_OVERRIDE_STATE_FILE="${incomplete_state_file}"
  TEST_LAUNCHCTL_DISABLE_FAIL_LABEL='io.github.casonk.traction-control.ci-repair-agentic-discovery'
  run_installer "${incomplete_case}" \
    --tier light --portfolio-root "${incomplete_portfolio}" --platform macos \
    --provider codex --no-clone --activate --state-dir "${incomplete_case}/state"
  TEST_LAUNCHCTL_BOOTSTRAP_FAIL_N=''
  TEST_LAUNCHCTL_BOOTSTRAP_COUNT_FILE=''
  TEST_LAUNCHCTL_BOOTSTRAP_PARTIAL_FAIL=''
  TEST_LAUNCHCTL_STATE_FILE=''
  TEST_LAUNCHCTL_OVERRIDE_STATE_FILE=''
  TEST_LAUNCHCTL_DISABLE_FAIL_LABEL=''
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "unverifiable launchd rollback still fails the activation"
  else
    fail_test "unverifiable launchd rollback still fails the activation"
  fi
  assert_output_contains 'launchd rollback could not verify the exact disabled-override map' \
    "unrestorable override map is reported, not silently accepted"
  assert_output_contains 'rollback was incomplete' \
    "activation failure distinguishes an incomplete rollback from a clean one"
  if grep -Fxq 'io.github.casonk.traction-control.ci-repair-agentic-discovery=false' \
    "${incomplete_state_file}"; then
    pass "incomplete rollback is detected while the override is still unrestored"
  else
    fail_test "incomplete rollback is detected while the override is still unrestored"
  fi
}

test_linux_render() {
  local case_root="${TEST_ROOT}/linux light"
  local portfolio_root="${case_root}/portfolio root"
  local unit_dir="${case_root}/systemd units"
  local actual_names="${case_root}/actual.txt"
  local expected_names="${case_root}/expected.txt"
  local clockwork_count

  prepare_profile_portfolio "${portfolio_root}" light
  mkdir -p "${unit_dir}"
  printf 'preserve me\n' > "${unit_dir}/unrelated.service"
  : > "${COMMAND_LOG}"

  run_installer "${case_root}" \
    --tier light \
    --portfolio-root "${portfolio_root}" \
    --platform linux \
    --provider codex \
    --model 'gpt-linux-test' \
    --no-clone \
    --state-dir "${case_root}/state" \
    --systemd-unit-dir "${unit_dir}"
  assert_status 0 "Linux light profile renders successfully through fake clockwork"

  find "${unit_dir}" -type f \( -name 'portfolio-audit-daily.*' -o -name 'bug-sweep-agentic.*' -o -name 'ci-repair-agentic-discovery.*' \) -print \
    | sed 's|.*/||' \
    | sort > "${actual_names}"
  cat > "${expected_names}" <<'EOF'
bug-sweep-agentic.service
bug-sweep-agentic.timer
ci-repair-agentic-discovery.service
ci-repair-agentic-discovery.timer
portfolio-audit-daily.service
portfolio-audit-daily.timer
EOF
  assert_line_sets_equal "${actual_names}" "${expected_names}" "Linux light renders three service/timer pairs"
  assert_file_contains "${unit_dir}/bug-sweep-agentic.service" 'BUG_SWEEP_AGENTIC_PROVIDER = "codex"' "Linux render propagates the provider into the manifest"
  assert_file_contains "${unit_dir}/bug-sweep-agentic.service" 'BUG_SWEEP_AGENTIC_MODEL = "gpt-linux-test"' "Linux render propagates the model into the manifest"
  assert_file_contains "${unit_dir}/unrelated.service" 'preserve me' "Linux rendering preserves unrelated units"
  clockwork_count="$(grep -c '^clockwork[[:space:]]' "${COMMAND_LOG}" 2>/dev/null || true)"
  if [[ "${clockwork_count}" == "3" ]]; then
    pass "Linux light delegates exactly three renders to clockwork"
  else
    fail_test "Linux light delegates exactly three renders to clockwork" "recorded ${clockwork_count} calls"
  fi
  assert_no_logged_command systemctl "Linux render-only mode never calls systemctl"
}

test_launchd_runner() {
  local case_root="${TEST_ROOT}/launchd runner"
  local runner="${REPO_ROOT}/scripts/run_traction_control_job.sh"

  mkdir -p "${case_root}"
  run_command env \
    'JOB_FIXTURE_VALUE=value with spaces & symbols' \
    JOB_FIXTURE_SECOND=second-value \
    /bin/bash "${runner}" \
    --job runner-test --schedule-kind none --jitter-seconds 0 -- \
    /bin/bash -c 'printf "%s|%s|%s\n" "${JOB_FIXTURE_VALUE}" "${JOB_FIXTURE_SECOND}" "$1"' \
    runner-command 'argument with spaces'
  assert_status 0 "launchd runner executes with the environment supplied by Clockwork"
  assert_output_contains 'value with spaces & symbols|second-value|argument with spaces' "launchd runner preserves environment values and spaced argv"

  run_command /bin/bash "${runner}" \
    --job runner-test --schedule-kind none --env-file "${case_root}/obsolete.env" -- \
    /usr/bin/true
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "launchd runner rejects the removed shell environment-file path"
  else
    fail_test "launchd runner rejects the removed shell environment-file path"
  fi
  assert_output_contains '--env-file was removed' "launchd runner points environment loading at Clockwork"

  run_command /bin/bash "${runner}" \
    --job runner-test --schedule-kind none --delay-seconds 900 -- \
    /usr/bin/true
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "launchd runner rejects the old per-firing fixed-delay path"
  else
    fail_test "launchd runner rejects the old per-firing fixed-delay path"
  fi
  assert_output_contains '--delay-seconds was removed' "launchd runner requires the boot-relative delay adapter"

  run_command /bin/bash "${runner}" \
    --job runner-test --schedule-kind none --jitter-seconds invalid -- \
    /usr/bin/true
  if [[ "${COMMAND_STATUS}" -ne 0 ]]; then
    pass "launchd runner rejects invalid jitter before command execution"
  else
    fail_test "launchd runner rejects invalid jitter before command execution"
  fi
}

smoke_wrapper() {
  local label="$1"
  local portfolio_root="$2"
  local log_dir="$3"
  shift 3
  mkdir -p "${log_dir}"
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${TEST_ROOT}/wrapper-home" \
    PORTFOLIO_ROOT="${portfolio_root}" \
    LOG_DIR="${log_dir}" \
    /bin/bash "$@"
  assert_status 0 "${label} executes safely under /bin/bash"
}

test_bash32_wrapper_smokes() {
  local empty_portfolio="${TEST_ROOT}/wrapper empty portfolio"
  local sample_portfolio="${TEST_ROOT}/wrapper sample portfolio"
  local forbidden_scripts
  local forbidden_hit=0

  mkdir -p "${empty_portfolio}" "${sample_portfolio}/sample/.git" "${sample_portfolio}/sample/.tachometer"
  printf '# refs fixture\n' > "${sample_portfolio}/sample/REFS-PUBLIC.md"
  printf 'print("fixture")\n' > "${sample_portfolio}/sample/sample.py"
  : > "${COMMAND_LOG}"

  forbidden_scripts="
${REPO_ROOT}/scripts/portfolio-audit.sh
${REPO_ROOT}/scripts/bug_sweep_agentic.sh
${REPO_ROOT}/scripts/ci_repair_agentic.sh
${REPO_ROOT}/scripts/refs_audit_agentic.sh
${REPO_ROOT}/scripts/tachometer_disk_pressure_agentic.sh
${REPO_ROOT}/scripts/template_consolidation_agentic.sh
${REPO_ROOT}/scripts/archility-daily.sh
${REPO_ROOT}/scripts/archility-weekly.sh
"
  while IFS= read -r script_path; do
    [[ -n "${script_path}" ]] || continue
    if grep -Eq '(^|[[:space:]])(mapfile|readarray|coproc)([[:space:]]|$)|declare[[:space:]]+-A|local[[:space:]]+-n' "${script_path}"; then
      forbidden_hit=1
    fi
  done <<< "${forbidden_scripts}"
  if (( forbidden_hit == 0 )); then
    pass "selected wrappers contain no known post-Bash-3.2 commands"
  else
    fail_test "selected wrappers contain no known post-Bash-3.2 commands"
  fi

  smoke_wrapper \
    "portfolio audit empty-inventory path" "${empty_portfolio}" "${TEST_ROOT}/logs/portfolio-empty" \
    "${REPO_ROOT}/scripts/portfolio-audit.sh"
  smoke_wrapper \
    "bug sweep empty-inventory path" "${empty_portfolio}" "${TEST_ROOT}/logs/bug-empty" \
    "${REPO_ROOT}/scripts/bug_sweep_agentic.sh" --provider codex
  smoke_wrapper \
    "CI discovery empty-inventory path" "${empty_portfolio}" "${TEST_ROOT}/logs/ci-empty" \
    "${REPO_ROOT}/scripts/ci_repair_agentic.sh" --provider codex --discovery-only
  smoke_wrapper \
    "REFS audit empty-inventory path" "${empty_portfolio}" "${TEST_ROOT}/logs/refs-empty" \
    "${REPO_ROOT}/scripts/refs_audit_agentic.sh" --provider codex
  smoke_wrapper \
    "tachometer empty-inventory path" "${empty_portfolio}" "${TEST_ROOT}/logs/tachometer-empty" \
    "${REPO_ROOT}/scripts/tachometer_disk_pressure_agentic.sh" --provider codex --dry-run
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${TEST_ROOT}/wrapper-home" \
    PORTFOLIO_ROOT="${empty_portfolio}" \
    LOG_DIR="${TEST_ROOT}/logs/archility-daily-empty" \
    ARCHILITY_CMD=/usr/bin/true \
    /bin/bash "${REPO_ROOT}/scripts/archility-daily.sh"
  assert_status 0 "archility daily empty-inventory path executes safely under /bin/bash"
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${TEST_ROOT}/wrapper-home" \
    PORTFOLIO_ROOT="${empty_portfolio}" \
    LOG_DIR="${TEST_ROOT}/logs/archility-weekly-empty" \
    ARCHILITY_CMD=/usr/bin/true \
    /bin/bash "${REPO_ROOT}/scripts/archility-weekly.sh"
  assert_status 0 "archility weekly empty-inventory path executes safely under /bin/bash"

  mkdir -p "${TEST_ROOT}/logs/portfolio"
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${TEST_ROOT}/wrapper-home" \
    PORTFOLIO_ROOT="${sample_portfolio}" \
    LOG_DIR="${TEST_ROOT}/logs/portfolio" \
    /bin/bash "${REPO_ROOT}/scripts/portfolio-audit.sh"
  assert_status 1 "portfolio audit completes its realistic gap-report path under /bin/bash"
  smoke_wrapper \
    "bug sweep inventory/provider path" "${sample_portfolio}" "${TEST_ROOT}/logs/bug" \
    "${REPO_ROOT}/scripts/bug_sweep_agentic.sh" --provider codex
  smoke_wrapper \
    "CI discovery path" "${sample_portfolio}" "${TEST_ROOT}/logs/ci" \
    "${REPO_ROOT}/scripts/ci_repair_agentic.sh" --provider codex --discovery-only
  smoke_wrapper \
    "REFS audit inventory/provider path" "${sample_portfolio}" "${TEST_ROOT}/logs/refs" \
    "${REPO_ROOT}/scripts/refs_audit_agentic.sh" --provider codex
  smoke_wrapper \
    "tachometer dry inventory path" "${sample_portfolio}" "${TEST_ROOT}/logs/tachometer" \
    "${REPO_ROOT}/scripts/tachometer_disk_pressure_agentic.sh" --provider codex --dry-run
  smoke_wrapper \
    "template consolidation provider path" "${sample_portfolio}" "${TEST_ROOT}/logs/template" \
    "${REPO_ROOT}/scripts/template_consolidation_agentic.sh" --provider codex

  mkdir -p "${TEST_ROOT}/logs/archility-daily" "${TEST_ROOT}/logs/archility-weekly"
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${TEST_ROOT}/wrapper-home" \
    PORTFOLIO_ROOT="${sample_portfolio}" \
    LOG_DIR="${TEST_ROOT}/logs/archility-daily" \
    ARCHILITY_CMD=/usr/bin/true \
    /bin/bash "${REPO_ROOT}/scripts/archility-daily.sh"
  assert_status 0 "archility daily executes safely under /bin/bash"
  run_command env \
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
    TEST_COMMAND_LOG="${COMMAND_LOG}" \
    HOME="${TEST_ROOT}/wrapper-home" \
    PORTFOLIO_ROOT="${sample_portfolio}" \
    LOG_DIR="${TEST_ROOT}/logs/archility-weekly" \
    ARCHILITY_CMD=/usr/bin/true \
    /bin/bash "${REPO_ROOT}/scripts/archility-weekly.sh"
  assert_status 0 "archility weekly executes safely under /bin/bash"
}

write_command_stubs

run_command /bin/bash -n "${INSTALLER}"
assert_status 0 "tiered installer parses under /bin/bash"
if [[ -x "${REPO_ROOT}/scripts/run_traction_control_job.sh" ]]; then
  pass "launchd runtime entry point is executable"
else
  fail_test "launchd runtime entry point is executable"
fi
run_command /bin/bash -n "${BASH_SOURCE[0]}"
assert_status 0 "integration test parses under /bin/bash"
run_command /bin/bash -n "${REPO_ROOT}/tests/test_install_traction_control_agents_containers.sh"
assert_status 0 "container host runner parses under /bin/bash"
run_command /bin/bash -n "${REPO_ROOT}/tests/containers/test-tier-install.sh"
assert_status 0 "container tier test parses under /bin/bash"
assert_file_contains \
  "${REPO_ROOT}/tests/containers/test-tier-install.sh" \
  'XDG_RUNTIME_DIR="${VERIFY_RUNTIME_ROOT}"' \
  "container user-unit verification provides a private runtime directory"
assert_file_contains \
  "${REPO_ROOT}/tests/containers/test-tier-install.sh" \
  'env -u DBUS_SESSION_BUS_ADDRESS' \
  "container user-unit verification cannot inherit a live user bus"
assert_file_contains \
  "${REPO_ROOT}/tests/containers/test-tier-install.sh" \
  '--recursive-errors=yes' \
  "container user-unit dependency errors remain fatal"

test_cli_and_dry_run
test_macos_tier light 3
test_macos_tier moderate 7
test_macos_tier heavy 9
test_autonomous_heavy_render
test_profile_reconciliation_render
test_repository_safety
test_linux_render
test_activation_with_stubs
test_reconciliation_fail_closed
test_launchd_transaction_failures
test_launchd_runner
test_bash32_wrapper_smokes

printf '# tests: %s passed, %s failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if (( FAIL_COUNT > 0 )); then
  exit 1
fi
