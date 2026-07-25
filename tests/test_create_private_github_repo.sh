#!/usr/bin/env bash
# Deterministic offline coverage for create_private_github_repo.sh.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WRAPPER="${REPO_ROOT}/scripts/create_private_github_repo.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/private-repo-wrapper-tests.XXXXXX")"
FAKE_BIN="${TEST_ROOT}/bin"
EMPTY_BIN="${TEST_ROOT}/empty-bin"
GH_LOG="${TEST_ROOT}/gh.log"

PASS_COUNT=0
FAIL_COUNT=0
COMMAND_OUTPUT=""
COMMAND_STATUS=0

cleanup() {
  case "${TEST_ROOT}" in
    "${TMPDIR:-/tmp}"/private-repo-wrapper-tests.*) rm -rf "${TEST_ROOT}" ;;
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
    fail_test "${label}" \
      "expected status ${expected}, got ${COMMAND_STATUS}: ${COMMAND_OUTPUT}"
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

assert_log_empty() {
  local label="$1"
  if [[ ! -s "${GH_LOG}" ]]; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected gh call: $(<"${GH_LOG}")"
  fi
}

assert_log_equals() {
  local expected="$1"
  local label="$2"
  local actual=""
  actual="$(<"${GH_LOG}")"
  if [[ "${actual}" == "${expected}" ]]; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected gh log: ${actual}"
  fi
}

assert_log_not_contains() {
  local unexpected="$1"
  local label="$2"
  if ! grep -F -- "${unexpected}" "${GH_LOG}" >/dev/null 2>&1; then
    pass "${label}"
  else
    fail_test "${label}" "unexpected gh argument: ${unexpected}"
  fi
}

mkdir -p "${FAKE_BIN}" "${EMPTY_BIN}"
cat > "${FAKE_BIN}/gh" <<'FAKE_GH'
#!/bin/bash
set -u

printf 'gh' >> "${FAKE_GH_LOG:?}"
for argument in "$@"; do
  printf '\t%s' "${argument}" >> "${FAKE_GH_LOG}"
done
printf '\n' >> "${FAKE_GH_LOG}"

if [[ "${1:-}" == "repo" && "${2:-}" == "create" ]]; then
  [[ $# -eq 4 && "${4:-}" == "--private" ]] || exit 64
  exit "${FAKE_GH_CREATE_STATUS:-0}"
fi

if [[ "${1:-}" == "repo" && "${2:-}" == "view" ]]; then
  [[ $# -eq 7 ]] || exit 64
  [[ "${4:-}" == "--json" && "${5:-}" == "isPrivate" ]] || exit 64
  [[ "${6:-}" == "--jq" && "${7:-}" == ".isPrivate" ]] || exit 64
  [[ "${FAKE_GH_VIEW_STATUS:-0}" == "0" ]] \
    || exit "${FAKE_GH_VIEW_STATUS}"
  printf '%s\n' "${FAKE_GH_IS_PRIVATE:-true}"
  exit 0
fi

exit 64
FAKE_GH
chmod 0755 "${FAKE_BIN}/gh"

run_wrapper() {
  : > "${GH_LOG}"
  COMMAND_OUTPUT="$(
    PATH="${FAKE_BIN}:/usr/bin:/bin" \
      FAKE_GH_LOG="${GH_LOG}" \
      FAKE_GH_CREATE_STATUS="${FAKE_GH_CREATE_STATUS:-0}" \
      FAKE_GH_VIEW_STATUS="${FAKE_GH_VIEW_STATUS:-0}" \
      FAKE_GH_IS_PRIVATE="${FAKE_GH_IS_PRIVATE:-true}" \
      /bin/bash "${WRAPPER}" "$@" 2>&1
  )"
  COMMAND_STATUS=$?
}

run_without_gh() {
  : > "${GH_LOG}"
  COMMAND_OUTPUT="$(
    PATH="${EMPTY_BIN}" /bin/bash "${WRAPPER}" "$@" 2>&1
  )"
  COMMAND_STATUS=$?
}

EXPECTED_SUCCESS_LOG=$'gh\trepo\tcreate\tcasonk/private-repository\t--private\ngh\trepo\tview\tcasonk/private-repository\t--json\tisPrivate\t--jq\t.isPrivate'

run_wrapper --help
assert_status 0 "help succeeds"
assert_output_contains \
  "Making a repository public is a separate, explicitly reviewed release action" \
  "help documents the separate public-release review"
assert_log_empty "help does not invoke gh"

run_wrapper
assert_failure "missing slug fails"
assert_log_empty "missing slug fails before gh"

run_wrapper casonk/one casonk/two
assert_failure "multiple slugs fail"
assert_log_empty "multiple slugs fail before gh"

for visibility_flag in --private --public --internal --visibility --visibility=public; do
  run_wrapper "${visibility_flag}" private-repository
  assert_failure "${visibility_flag} is rejected"
  assert_log_empty "${visibility_flag} is rejected before gh"
done

run_wrapper --description private-repository
assert_failure "arbitrary gh flags are rejected"
assert_log_empty "arbitrary gh flags never reach gh"

INVALID_SLUGS=(
  "private-repository"
  "/private-repository"
  "casonk/"
  "private-repository/extra"
  "-casonk/private-repository"
  "casonk-/private-repository"
  "case--onk/private-repository"
  "case_onk/private-repository"
  "casonk/-differential"
  "private-repository@main"
  "private-repository repo"
  "casonk/."
  "casonk/.."
  "private-repository --public"
  "ownerownerownerownerownerownerownerownerowner/private-repository"
  "casonk/repositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepository"
)

for invalid_slug in "${INVALID_SLUGS[@]}"; do
  run_wrapper "${invalid_slug}"
  assert_failure "invalid slug is rejected: ${invalid_slug}"
  assert_log_empty "invalid slug fails before gh: ${invalid_slug}"
done

run_without_gh private-repository
assert_failure "missing GitHub CLI fails closed"
assert_output_contains "GitHub CLI (gh) is required" \
  "missing GitHub CLI has an actionable error"
assert_log_empty "missing GitHub CLI cannot create a repository"

FAKE_GH_CREATE_STATUS=0
FAKE_GH_VIEW_STATUS=0
FAKE_GH_IS_PRIVATE=true
run_wrapper private-repository
assert_status 0 "private repository creation succeeds"
assert_output_contains \
  "created and verified private repository: private-repository" \
  "success is reported only after verification"
assert_log_equals "${EXPECTED_SUCCESS_LOG}" \
  "wrapper creates private then performs the exact privacy query"
assert_log_not_contains $'\t--push' "wrapper never asks gh to push"
assert_log_not_contains $'\t--source' "wrapper never selects a local source"
assert_log_not_contains $'\t--remote' "wrapper never creates a local remote"
assert_log_not_contains $'\t--public' "wrapper never requests public visibility"
assert_log_not_contains $'\t--internal' "wrapper never requests internal visibility"

FAKE_GH_IS_PRIVATE=true
run_wrapper acme-tools/.github
assert_status 0 "safe hyphenated owner and dot-prefixed repository are accepted"

FAKE_GH_CREATE_STATUS=42
run_wrapper private-repository
assert_failure "creation failure is propagated"
assert_output_contains "no source or commits were pushed" \
  "creation failure preserves the no-push boundary"
assert_log_equals $'gh\trepo\tcreate\tcasonk/private-repository\t--private' \
  "failed creation is not mistaken for a repository to verify"

FAKE_GH_CREATE_STATUS=0
FAKE_GH_VIEW_STATUS=43
run_wrapper private-repository
assert_failure "verification command failure is propagated"
assert_output_contains "do not push until its visibility is inspected" \
  "unverifiable visibility fails closed"
assert_log_equals "${EXPECTED_SUCCESS_LOG}" \
  "verification failure occurs after only create and view"

FAKE_GH_VIEW_STATUS=0
for private_result in false False "true " " true" "true
true"; do
  FAKE_GH_IS_PRIVATE="${private_result}"
  run_wrapper private-repository
  assert_failure "non-exact private result fails closed"
  assert_output_contains "did not report isPrivate as exactly true" \
    "non-exact result blocks the handoff"
done

printf '1..%s\n' "$(( PASS_COUNT + FAIL_COUNT ))"
if [[ "${FAIL_COUNT}" -ne 0 ]]; then
  printf '%s test(s) failed\n' "${FAIL_COUNT}" >&2
  exit 1
fi

printf 'all %s private-repository wrapper checks passed\n' "${PASS_COUNT}"
