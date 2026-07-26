#!/usr/bin/env bash
# Create a GitHub repository with a fail-closed private-first boundary.
#
# This wrapper deliberately exposes no source, remote, push, or visibility
# options. Publishing local content and changing repository visibility are
# separate operations with their own review requirements.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VISIBILITY_REGISTRY="${SCRIPT_DIR}/repository_visibility.py"
PRIVATE_REPOS_CONFIG="${TRACTION_CONTROL_PRIVATE_REPOS_CONFIG:-${REPO_ROOT}/config/repository-visibility/private.local.json}"
PUBLIC_REPOS_CONFIG="${TRACTION_CONTROL_PUBLIC_REPOS_CONFIG:-${REPO_ROOT}/config/repository-visibility/public.local.json}"

usage() {
  cat <<'EOF'
Usage: create_private_github_repo.sh OWNER/REPO

Create an empty GitHub repository as private, then verify that GitHub reports
its ID, canonical OWNER/REPO slug, and isPrivate as exactly true. The verified
repository is then recorded in the paired private/public visibility registry.

The wrapper does not add a Git remote, select a local source directory, or push
commits. It does not accept visibility flags: --private is enforced internally.
Making a repository public is a separate, explicitly reviewed release action
after history, references, examples, licensing, security policy, and private
data boundaries have been audited.

Registry paths default to:
  config/repository-visibility/private.local.json
  config/repository-visibility/public.local.json

Override them with TRACTION_CONTROL_PRIVATE_REPOS_CONFIG and
TRACTION_CONTROL_PUBLIC_REPOS_CONFIG. Both files must exist, form a valid pair,
and classify the requested repository as private or unclassified. A repository
classified public cannot be recreated by this private-first wrapper.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

validate_slug() {
  local slug="$1"
  local owner=""
  local repository=""

  case "${slug}" in
    */*) ;;
    *) fail "repository must use the OWNER/REPO form" ;;
  esac

  owner="${slug%%/*}"
  repository="${slug#*/}"

  [[ -n "${owner}" && -n "${repository}" && "${repository}" != */* ]] \
    || fail "repository must contain exactly one slash in OWNER/REPO form"

  (( ${#owner} <= 39 )) \
    || fail "GitHub owner must be 39 characters or fewer"
  [[ "${owner}" =~ ^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$ ]] \
    || fail "GitHub owner may contain only alphanumeric segments separated by single hyphens"

  (( ${#repository} <= 100 )) \
    || fail "GitHub repository name must be 100 characters or fewer"
  [[ "${repository}" =~ ^[A-Za-z0-9._][A-Za-z0-9._-]*$ ]] \
    || fail "GitHub repository name contains unsupported characters"
  [[ "${repository}" != "." && "${repository}" != ".." ]] \
    || fail "GitHub repository name cannot be . or .."
}

for argument in "$@"; do
  case "${argument}" in
    --private|--public|--internal|--visibility|--visibility=*)
      fail "visibility flags are not accepted; repositories are always created private"
      ;;
  esac
done

if [[ $# -eq 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
  usage
  exit 0
fi

[[ $# -eq 1 ]] || {
  usage >&2
  fail "exactly one OWNER/REPO slug is required"
}

REPOSITORY="$1"
validate_slug "${REPOSITORY}"

command -v python3 >/dev/null 2>&1 \
  || fail "Python 3 is required to validate the repository visibility registry"
[[ -f "${VISIBILITY_REGISTRY}" ]] \
  || fail "repository visibility registry tool is missing: ${VISIBILITY_REGISTRY}"

REGISTRY_OUTPUT=""
if ! REGISTRY_OUTPUT="$(
  python3 "${VISIBILITY_REGISTRY}" validate \
    --private "${PRIVATE_REPOS_CONFIG}" \
    --public "${PUBLIC_REPOS_CONFIG}" 2>&1
)"; then
  fail "repository visibility registry validation failed: ${REGISTRY_OUTPUT}"
fi

CLASSIFICATION=""
if ! CLASSIFICATION="$(
  python3 "${VISIBILITY_REGISTRY}" classify \
    --private "${PRIVATE_REPOS_CONFIG}" \
    --public "${PUBLIC_REPOS_CONFIG}" \
    --slug "${REPOSITORY}" 2>&1
)"; then
  fail "repository visibility classification failed: ${CLASSIFICATION}"
fi

case "${CLASSIFICATION}" in
  private|unclassified) ;;
  public)
    fail "repository is classified public; private-first creation is blocked"
    ;;
  *)
    fail "repository visibility classification returned an unexpected result"
    ;;
esac

command -v gh >/dev/null 2>&1 \
  || fail "GitHub CLI (gh) is required"

if ! GH_HOST=github.com gh repo create "${REPOSITORY}" --private; then
  fail "GitHub repository creation failed; no source or commits were pushed"
fi

REPOSITORY_METADATA=""
if ! REPOSITORY_METADATA="$(
  GH_HOST=github.com gh repo view "${REPOSITORY}" \
    --json id,nameWithOwner,isPrivate \
    --jq '[.id, .nameWithOwner, .isPrivate] | @tsv'
)"; then
  fail "created repository could not be verified; do not push until its visibility is inspected"
fi

[[ "${REPOSITORY_METADATA}" != *$'\n'* ]] \
  || fail "GitHub returned malformed repository metadata; do not push to the repository"

IFS=$'\t' read -r -a METADATA_FIELDS <<< "${REPOSITORY_METADATA}"
[[ ${#METADATA_FIELDS[@]} -eq 3 ]] \
  || fail "GitHub returned malformed repository metadata; do not push to the repository"

REPOSITORY_ID="${METADATA_FIELDS[0]}"
CANONICAL_REPOSITORY="${METADATA_FIELDS[1]}"
IS_PRIVATE="${METADATA_FIELDS[2]}"

[[ -n "${REPOSITORY_ID}" && "${REPOSITORY_ID}" != *[[:space:]]* ]] \
  || fail "GitHub did not return a valid repository ID; do not push to the repository"
[[ "${CANONICAL_REPOSITORY}" == "${REPOSITORY}" ]] \
  || fail "GitHub canonical repository slug did not exactly match ${REPOSITORY}; do not push to the repository"
[[ "${IS_PRIVATE}" == "true" ]] \
  || fail "GitHub did not report isPrivate as exactly true; do not push to the repository"

REGISTRY_OUTPUT=""
if ! REGISTRY_OUTPUT="$(
  python3 "${VISIBILITY_REGISTRY}" record-private \
    --private "${PRIVATE_REPOS_CONFIG}" \
    --public "${PUBLIC_REPOS_CONFIG}" \
    --id "${REPOSITORY_ID}" \
    --slug "${REPOSITORY}" 2>&1
)"; then
  fail "private repository was verified but could not be recorded in the visibility registry: ${REGISTRY_OUTPUT}"
fi

printf 'created, verified, and recorded private repository: %s\n' "${REPOSITORY}"
