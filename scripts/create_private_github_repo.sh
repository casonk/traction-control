#!/usr/bin/env bash
# Create a GitHub repository with a fail-closed private-first boundary.
#
# This wrapper deliberately exposes no source, remote, push, or visibility
# options. Publishing local content and changing repository visibility are
# separate operations with their own review requirements.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: create_private_github_repo.sh OWNER/REPO

Create an empty GitHub repository as private, then verify that GitHub reports
isPrivate as exactly true.

The wrapper does not add a Git remote, select a local source directory, or push
commits. It does not accept visibility flags: --private is enforced internally.
Making a repository public is a separate, explicitly reviewed release action
after history, references, examples, licensing, security policy, and private
data boundaries have been audited.
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

command -v gh >/dev/null 2>&1 \
  || fail "GitHub CLI (gh) is required"

if ! gh repo create "${REPOSITORY}" --private; then
  fail "GitHub repository creation failed; no source or commits were pushed"
fi

IS_PRIVATE=""
if ! IS_PRIVATE="$(gh repo view "${REPOSITORY}" --json isPrivate --jq .isPrivate)"; then
  fail "created repository could not be verified; do not push until its visibility is inspected"
fi

[[ "${IS_PRIVATE}" == "true" ]] \
  || fail "GitHub did not report isPrivate as exactly true; do not push to the repository"

printf 'created and verified private repository: %s\n' "${REPOSITORY}"
