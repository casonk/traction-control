#!/usr/bin/env bash
# Contract tests for the main-branch guard hooks and their installer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="${SCRIPT_DIR}/../scripts/git-hooks"
INSTALLER="${SCRIPT_DIR}/../scripts/install_main_branch_guard.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/main-branch-guard.XXXXXX")"
trap 'rm -rf "${TEST_ROOT}"' EXIT

export HOME="${TEST_ROOT}/home" XDG_CONFIG_HOME="${TEST_ROOT}/home/.config"
export GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
unset ALLOW_MAIN
mkdir -p "${HOME}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok - $*"; }

new_repo() {
  local dir="${TEST_ROOT}/$1"
  git init -q -b main "${dir}"
  git -C "${dir}" config core.hooksPath "${HOOKS}"
  echo "${dir}"
}

commit() { git -C "$1" commit -q --allow-empty -m "$2" 2>/dev/null; }

scratch="$(new_repo scratch)"
commit "${scratch}" "one" && commit "${scratch}" "two" ||
  fail "a repository without a GitHub remote should not be guarded"
pass "repository without a GitHub remote is not guarded"

fixture="$(new_repo fixture)"
git -C "${fixture}" remote add origin https://github.com/owner/fixture.git
commit "${fixture}" "one" && commit "${fixture}" "two" ||
  fail "a checkout in a temporary directory should not be guarded"
pass "checkout in a temporary directory is not guarded"

# Every repository below lives in TEST_ROOT, a temporary directory.
export MAIN_BRANCH_GUARD_IN_TMP=1

repo="$(new_repo plain)"
# The guard keys on a GitHub remote; this one is never contacted.
git -C "${repo}" remote add github git@github.com:owner/plain.git
commit "${repo}" "bootstrap" || fail "first commit on an unborn main should be allowed"
pass "first commit on unborn main"

commit "${repo}" "second" && fail "second commit on main should be refused"
pass "commit on main refused"

ALLOW_MAIN=1 commit "${repo}" "allowed" || fail "ALLOW_MAIN=1 should allow the commit"
pass "ALLOW_MAIN=1 allows a commit"

git -C "${repo}" switch -q -c feat/x
commit "${repo}" "feature" || fail "commit on a feature branch should be allowed"
pass "commit on feature branch"

git -C "${repo}" switch -q main
mkdir -p "${repo}/.github"
printf '[repository]\ndescription = "x"\n\n[main_branch]\nallow_direct = true # opted in\n' \
  >"${repo}/.github/repository.toml"
commit "${repo}" "opted in" || fail "allow_direct = true should allow the commit"
printf '[repository]\nallow_direct = true\n\n[main_branch]\nallow_direct = false\n' \
  >"${repo}/.github/repository.toml"
commit "${repo}" "opted out" && fail "allow_direct outside [main_branch] must not allow"
pass "allow_direct is read only from [main_branch]"
rm -rf "${repo}/.github"

marker="${TEST_ROOT}/local-hook-ran"
printf '#!/bin/sh\ntouch %s\n' "${marker}" >"${repo}/.git/hooks/pre-commit"
chmod +x "${repo}/.git/hooks/pre-commit"
git -C "${repo}" switch -q feat/x
commit "${repo}" "chained"
[ -f "${marker}" ] || fail "repo-local pre-commit hook should run after the guard"
printf '#!/bin/sh\nexit 1\n' >"${repo}/.git/hooks/pre-commit"
commit "${repo}" "local hook fails" && fail "a failing repo-local hook should still block"
rm "${repo}/.git/hooks/pre-commit"
pass "repo-local pre-commit hook is chained"

remote="${TEST_ROOT}/remote.git"
git init -q --bare -b main "${remote}"
git -C "${repo}" remote add origin "${remote}"
git -C "${repo}" push -q origin main 2>/dev/null || fail "creating the remote main should be allowed"
pass "initial push creates remote main"

git -C "${repo}" switch -q main
ALLOW_MAIN=1 commit "${repo}" "local main work"
git -C "${repo}" push -q origin main 2>/dev/null && fail "push to an existing remote main should be refused"
git -C "${repo}" push -q origin HEAD:refs/heads/main 2>/dev/null && fail "refspec push to main should be refused"
pass "push to remote main refused"

git -C "${repo}" push -q origin feat/x 2>/dev/null || fail "push of a feature branch should be allowed"
pass "push of feature branch"

ALLOW_MAIN=1 git -C "${repo}" push -q origin main 2>/dev/null || fail "ALLOW_MAIN=1 should allow the push"
pass "ALLOW_MAIN=1 allows a push"

git -C "${repo}" push -q origin :main 2>/dev/null && fail "deleting remote main should be refused"
pass "deleting remote main refused"

bare="${TEST_ROOT}/bare.git"
git clone -q --bare "${remote}" "${bare}"
git -C "${bare}" remote add github https://github.com/owner/plain.git
git -C "${bare}" config core.hooksPath "${HOOKS}"
err="$(git -C "${bare}" push -q "${remote}" feat/x:refs/heads/from-bare 2>&1)" ||
  fail "push of a feature branch from a bare repository should be allowed: ${err}"
[ -z "${err}" ] || fail "guard should be silent in a bare repository: ${err}"
err="$(git -C "${bare}" push -q -f "${remote}" refs/heads/feat/x:refs/heads/main 2>&1)" &&
  fail "push to main from a bare repository should be refused"
case "${err}" in *main-branch-guard:*) ;; *) fail "unexpected bare-repository refusal: ${err}" ;; esac
case "${err}" in *"work tree"* | *"null directory"*) fail "bare-repository refusal has errors: ${err}" ;; esac
err="$(MAIN_BRANCH_GUARD_IN_TMP=0 git -C "${bare}" push -q -f "${remote}" refs/heads/feat/x:refs/heads/main 2>&1)" ||
  fail "a bare repository in a temporary directory should not be guarded: ${err}"
[ -z "${err}" ] || fail "guard should be silent for a temporary bare repository: ${err}"
pass "bare repositories are guarded without errors"

bash "${INSTALLER}" >/dev/null
dest="${XDG_CONFIG_HOME}/git/main-branch-guard"
[ "$(git config --global core.hooksPath)" = "${dest}" ] || fail "installer should set core.hooksPath"
[ -x "${dest}/pre-commit" ] && [ -x "${dest}/pre-push" ] || fail "installer should copy hooks"
installed="${TEST_ROOT}/installed"
git init -q -b main "${installed}"
git -C "${installed}" remote add origin https://github.com/owner/installed.git
commit "${installed}" "bootstrap"
commit "${installed}" "second" && fail "installed hooks should guard main"
pass "installer sets a working global hooksPath"

git config --global core.hooksPath /elsewhere
bash "${INSTALLER}" >/dev/null 2>&1 && fail "installer should not replace another hooksPath without --force"
bash "${INSTALLER}" --force >/dev/null || fail "--force should replace hooksPath"
bash "${INSTALLER}" --uninstall >/dev/null
git config --global --get core.hooksPath >/dev/null && fail "--uninstall should unset hooksPath"
pass "installer --force and --uninstall"

echo "all main-branch guard tests passed"
