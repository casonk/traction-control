#!/usr/bin/env bash
# Contract tests for the commit-msg hook that refuses AI attribution (portfolio rule 13).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS="${SCRIPT_DIR}/../scripts/git-hooks"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/commit-msg-guard.XXXXXX")"
trap 'rm -rf "${TEST_ROOT}"' EXIT

export HOME="${TEST_ROOT}/home" XDG_CONFIG_HOME="${TEST_ROOT}/home/.config"
export GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com
export GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
mkdir -p "${HOME}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "ok - $*"; }

# Attribution strings are assembled from fragments so this file never carries
# the literal text that the hook (and the repository scanners) look for.
co="Co-Auth""ored-By"
who="Cla""ude"
mail="noreply@anth""ropic.com"
sess="Cla""ude-Session"
gen="Gener""ated with"

repo="${TEST_ROOT}/repo"
git init -q -b feature "${repo}"
git -C "${repo}" config core.hooksPath "${HOOKS}"

try() { git -C "${repo}" commit -q --allow-empty -m "$1" 2>/dev/null; }
refused() { try "$1" && fail "should refuse: $2"; pass "refuses $2"; }
allowed() { try "$1" || fail "should allow: $2"; pass "allows $2"; }

refused "$(printf 'feat: x\n\n%s: %s <%s>' "${co}" "${who}" "${mail}")" "an AI co-author trailer"
refused "$(printf 'feat: x\n\n%s: Some Bot <%s>' "${co}" "${mail}")" "an Anthropic noreply address"
refused "$(printf 'feat: x\n\n%s: https://example.test/s/1' "${sess}")" "a session trailer"
refused "$(printf 'feat: x\n\n%s [%s Code](https://example.test)' "${gen}" "${who}")" "a generated-with footer"
refused "$(printf 'feat: x\n\nAssisted-by: some tool')" "an assisted-by trailer"
refused "$(printf 'feat: x\n\nsee https://claude.ai/code/session_1')" "a hosted session link"

allowed "$(printf 'feat: x\n\n%s: Real Person <person@example.com>' "${co}")" "a human co-author"
allowed "docs: explain how the ${who} API client retries" "an ordinary mention of a model name"
allowed "fix: keep the cursor visible after a resize" "unrelated words that resemble tool names"

# A repository's own commit-msg hook still runs after the attribution check passes.
printf '#!/usr/bin/env bash\nexit 7\n' >"${repo}/.git/hooks/commit-msg"
chmod +x "${repo}/.git/hooks/commit-msg"
try "feat: chained" && fail "the repo-local commit-msg hook should still run"
pass "chains to the repo-local commit-msg hook"

echo "all commit-msg guard tests passed"
