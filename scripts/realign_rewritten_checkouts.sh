#!/usr/bin/env bash
# Move every checkout under a portfolio root onto its rewritten origin history.
#
# After a history rewrite and force-push, each other clone still holds the old
# commits. Its branch has diverged from origin, so `git pull` fails, and a
# careless push would publish the old history again. This script finds those
# clones and resets them, but only when doing so cannot lose work:
#
#   - the checkout is on its default branch;
#   - its HEAD tree is identical to the tree of some commit on the origin
#     branch (a rewrite that only changes messages or metadata keeps trees),
#     so the checkout has no local-only commits;
#   - uncommitted and untracked files, if any, do not overlap the files that
#     differ between HEAD and the origin branch.
#
# Before resetting, the old HEAD is kept as a local `legacy/pre-rewrite-<tag>`
# branch. Never push those branches: they hold exactly the history the rewrite
# removed.
#
# It also fast-forwards clean checkouts that are simply behind. Anything else is
# reported and left alone. Without --apply nothing changes.
#
# Repositories are discovered, not listed, so this tracked script names none.
# Bash 3.2 compatible so it runs unchanged on macOS. Because a clone on old
# history cannot pull this file, it can also be run straight from origin, from
# the portfolio root (bash or zsh):
#
#   git -C util-repos/traction-control fetch origin
#   bash <(git -C util-repos/traction-control show origin/main:scripts/realign_rewritten_checkouts.sh)
#
# Use process substitution rather than piping into `bash -s`: an SSH fetch
# reads standard input and would swallow the rest of a piped script.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: realign_rewritten_checkouts.sh [--apply] [--root DIR] [--tag TAG] [--no-fetch]

  --root DIR   portfolio root to scan (default: current directory)
  --apply      make the changes; without it, only report the plan
  --tag TAG    suffix for the legacy/pre-rewrite-<TAG> backup branch
               (default: today's date as YYYYMMDD)
  --no-fetch   use the remote-tracking refs already present
EOF
}

ROOT="${PWD}"
APPLY=0
FETCH=1
TAG="$(date +%Y%m%d)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --no-fetch) FETCH=0 ;;
    --root) ROOT="${2:?--root needs a directory}"; shift ;;
    --tag) TAG="${2:?--tag needs a value}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
case "${TAG}" in
  *[!A-Za-z0-9._-]*|'') echo "invalid --tag: ${TAG}" >&2; exit 2 ;;
esac
ROOT="$(cd "${ROOT}" && pwd)"

counts_ok=0
counts_skip=0
counts_fail=0

report() {  # <repo> <message>
  printf '%-48s %s\n' "$1" "$2"
}

# True when "$1" (newline-separated paths) and "$2" share an entry.
overlaps() {
  local path
  while IFS= read -r path; do
    [[ -n "${path}" ]] || continue
    if printf '%s\n' "$2" | grep -xF -- "${path}" >/dev/null; then
      return 0
    fi
  done <<< "$1"
  return 1
}

realign_one() {  # <absolute repo dir> <display name>
  local repo="$1" name="$2"
  local g=(git -C "${repo}")

  if ! "${g[@]}" remote get-url origin >/dev/null 2>&1; then
    report "${name}" "no origin, skipped"; counts_skip=$((counts_skip + 1)); return
  fi
  if [[ "${FETCH}" == 1 ]] && ! "${g[@]}" fetch -q --force --tags origin </dev/null 2>/dev/null; then
    report "${name}" "FETCH FAILED"; counts_fail=$((counts_fail + 1)); return
  fi

  local default
  default="$("${g[@]}" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  default="${default#origin/}"
  [[ -n "${default}" ]] || default=main
  local upstream="refs/remotes/origin/${default}"
  if ! "${g[@]}" rev-parse -q --verify "${upstream}" >/dev/null; then
    report "${name}" "no origin/${default}, skipped"; counts_skip=$((counts_skip + 1)); return
  fi

  local branch
  branch="$("${g[@]}" branch --show-current)"
  if [[ "${branch}" != "${default}" ]]; then
    report "${name}" "on '${branch:-detached HEAD}', not ${default}; skipped"
    counts_skip=$((counts_skip + 1)); return
  fi

  local head target
  head="$("${g[@]}" rev-parse HEAD)"
  target="$("${g[@]}" rev-parse "${upstream}")"
  if [[ "${head}" == "${target}" ]]; then
    report "${name}" "up to date"; counts_ok=$((counts_ok + 1)); return
  fi

  # Paths with uncommitted or untracked changes (rename targets included).
  local dirty
  dirty="$("${g[@]}" status --porcelain --untracked-files=all | cut -c4- | sed 's/.* -> //')"

  if "${g[@]}" merge-base --is-ancestor "${head}" "${target}"; then
    if [[ -n "${dirty}" ]]; then
      report "${name}" "behind origin but has local changes; skipped"
      counts_skip=$((counts_skip + 1)); return
    fi
    if [[ "${APPLY}" == 1 ]]; then
      "${g[@]}" merge -q --ff-only "${upstream}"
      report "${name}" "fast-forwarded"
    else
      report "${name}" "behind origin: would fast-forward"
    fi
    counts_ok=$((counts_ok + 1)); return
  fi
  if "${g[@]}" merge-base --is-ancestor "${target}" "${head}"; then
    report "${name}" "ahead of origin (unpushed commits); skipped"
    counts_skip=$((counts_skip + 1)); return
  fi

  # Diverged. Safe only if HEAD's content was published under a new ID.
  local tree
  tree="$("${g[@]}" rev-parse "HEAD^{tree}")"
  # Not `grep -q`: it stops reading at the first match, git log then dies of
  # SIGPIPE, and pipefail turns a found tree into "not found" on any history
  # longer than a few kilobytes of output.
  if ! "${g[@]}" log --format=%T "${upstream}" | grep -xF "${tree}" >/dev/null; then
    report "${name}" "diverged with local-only commits; skipped"
    counts_skip=$((counts_skip + 1)); return
  fi

  local changed
  changed="$("${g[@]}" diff --name-only HEAD "${upstream}")"
  if [[ -n "${dirty}" ]] && overlaps "${dirty}" "${changed}"; then
    report "${name}" "rewritten, but local changes touch files origin changed; skipped"
    counts_skip=$((counts_skip + 1)); return
  fi

  local backup="legacy/pre-rewrite-${TAG}"
  local existing
  existing="$("${g[@]}" rev-parse -q --verify "refs/heads/${backup}" || true)"
  if [[ -n "${existing}" && "${existing}" != "${head}" ]]; then
    report "${name}" "${backup} already exists at another commit; skipped"
    counts_skip=$((counts_skip + 1)); return
  fi

  if [[ "${APPLY}" == 0 ]]; then
    if [[ -n "${dirty}" ]]; then
      report "${name}" "rewritten: would keep ${backup} and move to origin (local changes kept)"
    else
      report "${name}" "rewritten: would keep ${backup} and move to origin"
    fi
    counts_ok=$((counts_ok + 1)); return
  fi

  [[ -n "${existing}" ]] || "${g[@]}" branch "${backup}" "${head}"
  if [[ -z "${dirty}" ]]; then
    "${g[@]}" reset -q --hard "${upstream}"
    report "${name}" "moved to origin (old HEAD kept as ${backup})"
  else
    # Keep the working tree, then bring the files origin changed (none of
    # which are locally modified) up to the new HEAD.
    "${g[@]}" reset -q --mixed "${upstream}"
    local path
    while IFS= read -r path; do
      [[ -n "${path}" ]] || continue
      if "${g[@]}" cat-file -e "HEAD:${path}" 2>/dev/null; then
        "${g[@]}" checkout -q HEAD -- "${path}"
      else
        rm -f -- "${repo}/${path}"
      fi
    done <<< "${changed}"
    report "${name}" "moved to origin, local changes kept (old HEAD kept as ${backup})"
  fi
  counts_ok=$((counts_ok + 1))
}

echo "portfolio root: ${ROOT}"
[[ "${APPLY}" == 1 ]] || echo "dry run: pass --apply to make these changes"
echo

while IFS= read -r gitdir; do
  repo="${gitdir%/.git}"
  realign_one "${repo}" "${repo#"${ROOT}"/}"
done < <(
  find "${ROOT}" -maxdepth 4 -type d -name .git \
    ! -path "*/archive-repos/*" ! -path "*/vendor/*" ! -path "*/node_modules/*" 2>/dev/null | sort
)

echo
echo "ok ${counts_ok}, skipped ${counts_skip}, failed ${counts_fail}"
[[ "${counts_fail}" == 0 ]]
