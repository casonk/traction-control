#!/usr/bin/env bash
# Deterministic offline coverage for create_private_github_repo.sh.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WRAPPER="${REPO_ROOT}/scripts/create_private_github_repo.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/private-repo-wrapper-tests.XXXXXX")"
FAKE_BIN="${TEST_ROOT}/bin"
NO_GH_BIN="${TEST_ROOT}/no-gh-bin"
GH_LOG="${TEST_ROOT}/gh.log"
REGISTRY_ROOT="${TEST_ROOT}/registry"
PRIVATE_CONFIG="${REGISTRY_ROOT}/private.local.json"
PUBLIC_CONFIG="${REGISTRY_ROOT}/public.local.json"
ORIGINAL_PATH="${PATH}"

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

assert_registry_contains() {
  local file="$1"
  local expected="$2"
  local label="$3"
  if grep -F -- "${expected}" "${file}" >/dev/null 2>&1; then
    pass "${label}"
  else
    fail_test "${label}" "missing registry content in ${file}: ${expected}"
  fi
}

assert_files_equal() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if cmp -s "${expected}" "${actual}"; then
    pass "${label}"
  else
    fail_test "${label}" "file changed unexpectedly: ${actual}"
  fi
}

write_registry_pair() {
  local private_repositories="${1:-[]}"
  local public_repositories="${2:-[]}"

  cat > "${PRIVATE_CONFIG}" <<EOF
{
  "schema_version": 1,
  "registry_id": "traction-control-test",
  "generation": 7,
  "visibility": "private",
  "repositories": ${private_repositories}
}
EOF
  cat > "${PUBLIC_CONFIG}" <<EOF
{
  "schema_version": 1,
  "registry_id": "traction-control-test",
  "generation": 7,
  "visibility": "public",
  "repositories": ${public_repositories}
}
EOF
  chmod 0600 "${PRIVATE_CONFIG}" "${PUBLIC_CONFIG}"
}

mkdir -p "${FAKE_BIN}" "${NO_GH_BIN}" "${REGISTRY_ROOT}"
ln -s "$(command -v dirname)" "${NO_GH_BIN}/dirname"
ln -s "$(command -v git)" "${NO_GH_BIN}/git"
ln -s "$(command -v python3)" "${NO_GH_BIN}/python3"

cat > "${FAKE_BIN}/gh" <<'FAKE_GH'
#!/bin/bash
set -u

[[ "${GH_HOST:-}" == "github.com" ]] || exit 65

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
  [[ "${4:-}" == "--json" && "${5:-}" == "id,nameWithOwner,isPrivate" ]] \
    || exit 64
  [[ "${6:-}" == "--jq" \
    && "${7:-}" == "[.id, .nameWithOwner, .isPrivate] | @tsv" ]] \
    || exit 64
  [[ "${FAKE_GH_VIEW_STATUS:-0}" == "0" ]] \
    || exit "${FAKE_GH_VIEW_STATUS}"
  printf '%s\t%s\t%s\n' \
    "${FAKE_GH_REPO_ID-R_test123}" \
    "${FAKE_GH_NAME_WITH_OWNER:-${3:-}}" \
    "${FAKE_GH_IS_PRIVATE:-true}"
  exit 0
fi

exit 64
FAKE_GH
chmod 0755 "${FAKE_BIN}/gh"

run_wrapper() {
  : > "${GH_LOG}"
  COMMAND_OUTPUT="$(
    PATH="${FAKE_BIN}:${ORIGINAL_PATH}" \
      GH_HOST="enterprise.example.invalid" \
      TRACTION_CONTROL_PRIVATE_REPOS_CONFIG="${TEST_PRIVATE_CONFIG:-${PRIVATE_CONFIG}}" \
      TRACTION_CONTROL_PUBLIC_REPOS_CONFIG="${TEST_PUBLIC_CONFIG:-${PUBLIC_CONFIG}}" \
      FAKE_GH_LOG="${GH_LOG}" \
      FAKE_GH_CREATE_STATUS="${FAKE_GH_CREATE_STATUS:-0}" \
      FAKE_GH_VIEW_STATUS="${FAKE_GH_VIEW_STATUS:-0}" \
      FAKE_GH_REPO_ID="${FAKE_GH_REPO_ID-R_test123}" \
      FAKE_GH_NAME_WITH_OWNER="${FAKE_GH_NAME_WITH_OWNER:-}" \
      FAKE_GH_IS_PRIVATE="${FAKE_GH_IS_PRIVATE:-true}" \
      /bin/bash "${WRAPPER}" "$@" 2>&1
  )"
  COMMAND_STATUS=$?
}

run_without_gh() {
  : > "${GH_LOG}"
  COMMAND_OUTPUT="$(
    PATH="${NO_GH_BIN}" \
      TRACTION_CONTROL_PRIVATE_REPOS_CONFIG="${PRIVATE_CONFIG}" \
      TRACTION_CONTROL_PUBLIC_REPOS_CONFIG="${PUBLIC_CONFIG}" \
      /bin/bash "${WRAPPER}" "$@" 2>&1
  )"
  COMMAND_STATUS=$?
}

write_registry_pair

EXPECTED_SUCCESS_LOG=$'gh\trepo\tcreate\texample-owner/private-agent\t--private\ngh\trepo\tview\texample-owner/private-agent\t--json\tid,nameWithOwner,isPrivate\t--jq\t[.id, .nameWithOwner, .isPrivate] | @tsv'

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
  run_wrapper "${visibility_flag}" example-owner/private-agent
  assert_failure "${visibility_flag} is rejected"
  assert_log_empty "${visibility_flag} is rejected before gh"
done

run_wrapper --description example-owner/private-agent
assert_failure "arbitrary gh flags are rejected"
assert_log_empty "arbitrary gh flags never reach gh"

INVALID_SLUGS=(
  "private-agent"
  "/private-agent"
  "casonk/"
  "example-owner/private-agent/extra"
  "-example-owner/private-agent"
  "casonk-/private-agent"
  "case--onk/private-agent"
  "case_onk/private-agent"
  "casonk/-private-agent"
  "example-owner/private-agent@main"
  "example-owner/private-agent repo"
  "casonk/."
  "casonk/.."
  "example-owner/private-agent --public"
  "ownerownerownerownerownerownerownerownerowner/private-agent"
  "casonk/repositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepositoryrepository"
)

for invalid_slug in "${INVALID_SLUGS[@]}"; do
  run_wrapper "${invalid_slug}"
  assert_failure "invalid slug is rejected: ${invalid_slug}"
  assert_log_empty "invalid slug fails before gh: ${invalid_slug}"
done

TEST_PRIVATE_CONFIG="${REGISTRY_ROOT}/missing-private.json"
run_wrapper example-owner/private-agent
assert_failure "missing private registry fails closed"
assert_output_contains "registry validation failed" \
  "missing private registry reports a validation failure"
assert_log_empty "missing private registry fails before gh"
unset TEST_PRIVATE_CONFIG

TEST_PUBLIC_CONFIG="${REGISTRY_ROOT}/missing-public.json"
run_wrapper example-owner/private-agent
assert_failure "missing public registry fails closed"
assert_output_contains "registry validation failed" \
  "missing public registry reports a validation failure"
assert_log_empty "missing public registry fails before gh"
unset TEST_PUBLIC_CONFIG

printf '{malformed\n' > "${PRIVATE_CONFIG}"
run_wrapper example-owner/private-agent
assert_failure "malformed registry fails closed"
assert_output_contains "registry validation failed" \
  "malformed registry reports a validation failure"
assert_log_empty "malformed registry fails before gh"

write_registry_pair \
  '[]' \
  '[{"id":"R_public123","slug":"example-owner/private-agent"}]'
cp "${PRIVATE_CONFIG}" "${TEST_ROOT}/public-block-private.before"
cp "${PUBLIC_CONFIG}" "${TEST_ROOT}/public-block-public.before"
run_wrapper example-owner/private-agent
assert_failure "public classification blocks private creation"
assert_output_contains "classified public" \
  "public classification explains the policy boundary"
assert_log_empty "public classification blocks before gh"
assert_files_equal "${TEST_ROOT}/public-block-private.before" "${PRIVATE_CONFIG}" \
  "public classification does not mutate the private registry"
assert_files_equal "${TEST_ROOT}/public-block-public.before" "${PUBLIC_CONFIG}" \
  "public classification does not mutate the public registry"

write_registry_pair
run_without_gh example-owner/private-agent
assert_failure "missing GitHub CLI fails closed"
assert_output_contains "GitHub CLI (gh) is required" \
  "missing GitHub CLI has an actionable error"
assert_log_empty "missing GitHub CLI cannot create a repository"

write_registry_pair
FAKE_GH_CREATE_STATUS=0
FAKE_GH_VIEW_STATUS=0
FAKE_GH_REPO_ID=R_test123
FAKE_GH_NAME_WITH_OWNER=example-owner/private-agent
FAKE_GH_IS_PRIVATE=true
run_wrapper example-owner/private-agent
assert_status 0 "private repository creation succeeds"
assert_output_contains \
  "created, verified, and recorded private repository: example-owner/private-agent" \
  "success is reported only after verification and registry recording"
assert_log_equals "${EXPECTED_SUCCESS_LOG}" \
  "wrapper creates private then performs the exact metadata query"
assert_log_not_contains $'\t--push' "wrapper never asks gh to push"
assert_log_not_contains $'\t--source' "wrapper never selects a local source"
assert_log_not_contains $'\t--remote' "wrapper never creates a local remote"
assert_log_not_contains $'\t--public' "wrapper never requests public visibility"
assert_log_not_contains $'\t--internal' "wrapper never requests internal visibility"
assert_registry_contains "${PRIVATE_CONFIG}" '"id": "R_test123"' \
  "successful creation records the GitHub repository ID"
assert_registry_contains "${PRIVATE_CONFIG}" '"slug": "example-owner/private-agent"' \
  "successful creation records the canonical repository slug"
assert_registry_contains "${PRIVATE_CONFIG}" '"generation": 8' \
  "successful creation advances the private registry generation"
assert_registry_contains "${PUBLIC_CONFIG}" '"generation": 8' \
  "successful creation advances the paired public registry generation"

write_registry_pair \
  '[{"id":"R_existing123","slug":"acme-tools/.github"}]' \
  '[]'
FAKE_GH_REPO_ID=R_existing123
FAKE_GH_NAME_WITH_OWNER=acme-tools/.github
FAKE_GH_IS_PRIVATE=true
run_wrapper acme-tools/.github
assert_status 0 \
  "an existing private classification may use the private-first creator"
assert_registry_contains "${PRIVATE_CONFIG}" '"generation": 7' \
  "recording an exact existing private identity is idempotent"

write_registry_pair
cp "${PRIVATE_CONFIG}" "${TEST_ROOT}/create-failure-private.before"
cp "${PUBLIC_CONFIG}" "${TEST_ROOT}/create-failure-public.before"
FAKE_GH_NAME_WITH_OWNER=example-owner/private-agent
FAKE_GH_REPO_ID=R_test123
FAKE_GH_CREATE_STATUS=42
run_wrapper example-owner/private-agent
assert_failure "creation failure is propagated"
assert_output_contains "no source or commits were pushed" \
  "creation failure preserves the no-push boundary"
assert_log_equals $'gh\trepo\tcreate\texample-owner/private-agent\t--private' \
  "failed creation is not mistaken for a repository to verify"
assert_files_equal "${TEST_ROOT}/create-failure-private.before" "${PRIVATE_CONFIG}" \
  "creation failure does not record a private repository"
assert_files_equal "${TEST_ROOT}/create-failure-public.before" "${PUBLIC_CONFIG}" \
  "creation failure does not advance the paired registry"

write_registry_pair
cp "${PRIVATE_CONFIG}" "${TEST_ROOT}/view-failure-private.before"
cp "${PUBLIC_CONFIG}" "${TEST_ROOT}/view-failure-public.before"
FAKE_GH_CREATE_STATUS=0
FAKE_GH_VIEW_STATUS=43
run_wrapper example-owner/private-agent
assert_failure "verification command failure is propagated"
assert_output_contains "do not push until its visibility is inspected" \
  "unverifiable visibility fails closed"
assert_log_equals "${EXPECTED_SUCCESS_LOG}" \
  "verification failure occurs after only create and view"
assert_files_equal "${TEST_ROOT}/view-failure-private.before" "${PRIVATE_CONFIG}" \
  "verification failure does not record a private repository"
assert_files_equal "${TEST_ROOT}/view-failure-public.before" "${PUBLIC_CONFIG}" \
  "verification failure does not advance the paired registry"

write_registry_pair
cp "${PRIVATE_CONFIG}" "${TEST_ROOT}/privacy-failure-private.before"
cp "${PUBLIC_CONFIG}" "${TEST_ROOT}/privacy-failure-public.before"
FAKE_GH_VIEW_STATUS=0
for private_result in false False "true " " true"; do
  FAKE_GH_IS_PRIVATE="${private_result}"
  run_wrapper example-owner/private-agent
  assert_failure "non-exact private result fails closed"
  assert_output_contains "did not report isPrivate as exactly true" \
    "non-exact result blocks the handoff"
done
FAKE_GH_IS_PRIVATE=$'true\ntrue'
run_wrapper example-owner/private-agent
assert_failure "multiline repository metadata fails closed"
assert_output_contains "malformed repository metadata" \
  "multiline metadata blocks the handoff"
assert_files_equal "${TEST_ROOT}/privacy-failure-private.before" "${PRIVATE_CONFIG}" \
  "privacy verification failures never record the repository"
assert_files_equal "${TEST_ROOT}/privacy-failure-public.before" "${PUBLIC_CONFIG}" \
  "privacy verification failures never advance the paired registry"

write_registry_pair
FAKE_GH_IS_PRIVATE=true
FAKE_GH_NAME_WITH_OWNER=Example-Owner/private-agent
run_wrapper example-owner/private-agent
assert_failure "non-exact canonical slug fails closed"
assert_output_contains "canonical repository slug did not exactly match" \
  "canonical slug mismatch blocks registry recording"
assert_registry_contains "${PRIVATE_CONFIG}" '"generation": 7' \
  "canonical slug mismatch leaves the private registry unchanged"

write_registry_pair
FAKE_GH_NAME_WITH_OWNER=example-owner/private-agent
FAKE_GH_REPO_ID=""
run_wrapper example-owner/private-agent
assert_failure "missing GitHub repository ID fails closed"
assert_output_contains "malformed repository metadata" \
  "missing repository ID blocks registry recording"
assert_registry_contains "${PRIVATE_CONFIG}" '"generation": 7' \
  "missing repository ID leaves the private registry unchanged"

FAKE_GH_REPO_ID=" "
run_wrapper example-owner/private-agent
assert_failure "invalid GitHub repository ID fails closed"
assert_output_contains "valid repository ID" \
  "whitespace repository ID blocks registry recording"
assert_registry_contains "${PRIVATE_CONFIG}" '"generation": 7' \
  "invalid repository IDs leave the private registry unchanged"

printf '1..%s\n' "$(( PASS_COUNT + FAIL_COUNT ))"
if [[ "${FAIL_COUNT}" -ne 0 ]]; then
  printf '%s test(s) failed\n' "${FAIL_COUNT}" >&2
  exit 1
fi

printf 'all %s private-repository wrapper checks passed\n' "${PASS_COUNT}"
