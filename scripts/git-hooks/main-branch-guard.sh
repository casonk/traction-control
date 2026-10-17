# shellcheck shell=bash
# Shared logic for the portfolio main-branch guard hooks.
#
# In checkouts with a GitHub remote, direct commits and pushes to main/master
# are refused unless one of these
# explicitly allows them:
#   - ALLOW_MAIN=1 in the environment (one command, one approval)
#   - [main_branch] allow_direct = true in the repo's .github/repository.toml
#
# After the guard passes, the repo's own .git/hooks/<name> runs, because a
# global core.hooksPath otherwise hides repo-local hooks.

# The checkout's top level, or the Git directory of a bare repository, which
# has no working tree for --show-toplevel to report.
guard_location() {
  local dir
  if [ "$(git rev-parse --is-bare-repository 2>/dev/null)" = "true" ]; then
    dir="$(git rev-parse --absolute-git-dir 2>/dev/null)"
  else
    dir="$(git rev-parse --show-toplevel 2>/dev/null)"
  fi
  [ -n "${dir}" ] && (cd "${dir}" && pwd -P)
}

guard_applies() {
  # Only real checkouts are guarded: they have a GitHub remote and do not live
  # in a temporary directory. Test fixtures and scratch repositories commit to
  # main freely. MAIN_BRANCH_GUARD_IN_TMP=1 guards temporary checkouts too.
  git remote -v 2>/dev/null | grep 'github\.com[:/]' >/dev/null || return 1
  [ "${MAIN_BRANCH_GUARD_IN_TMP:-}" = "1" ] && return 0
  local top tmp
  top="$(guard_location)" || return 1
  for tmp in "${TMPDIR:-}" /tmp /var/tmp; do
    [ -n "${tmp}" ] && [ -d "${tmp}" ] || continue
    tmp="$(cd "${tmp}" && pwd -P)"
    case "${top}/" in
      "${tmp%/}/"*) return 1 ;;
    esac
  done
  return 0
}

guard_protected_branch() {
  guard_applies || return 1
  case "$1" in
    main | master) return 0 ;;
    *) return 1 ;;
  esac
}

guard_allowed() {
  [ "${ALLOW_MAIN:-}" = "1" ] && return 0
  local top settings
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 1
  settings="${top}/.github/repository.toml"
  [ -f "${settings}" ] || return 1
  awk '
    /^[[:space:]]*\[/ { section = $0; gsub(/[[:space:]]/, "", section); next }
    section == "[main_branch]" && /^[[:space:]]*allow_direct[[:space:]]*=[[:space:]]*true[[:space:]]*(#.*)?$/ { found = 1 }
    END { exit found ? 0 : 1 }
  ' "${settings}"
}

guard_refuse() {
  cat >&2 <<EOF
main-branch-guard: $1
  Work on a branch and open a pull request instead:
    git switch -c <type>/<topic>
  To allow this once, rerun with ALLOW_MAIN=1.
EOF
  exit 1
}

guard_chain() {
  local name="$1"
  shift
  local hook
  hook="$(git rev-parse --git-common-dir)/hooks/${name}"
  if [ -x "${hook}" ]; then
    exec "${hook}" ${1+"$@"}
  fi
  exit 0
}
