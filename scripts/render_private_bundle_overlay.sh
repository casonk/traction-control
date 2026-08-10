#!/usr/bin/env bash
# Render the owner-only local support bundle that merges the tracked public
# base with an ignored private-repository overlay.
#
# The tracked config/traction-control-agents/repos.conf can only ever name
# public repositories: it is a tracked file in a public repo, and the private
# disclosure gate treats a private slug there as a leak. This script is the
# second deployment workflow for private support repos. It keeps their names
# in an ignored, owner-only local file, verifies every one of them against the
# private visibility registry, and renders a merged bundle that is passed to
# install_traction_control_agents.sh with --repo-config.
#
# It is fail-closed. A slug that the registry classifies as public or
# unclassified is refused, as is an overlay file that Git tracks or that sits
# unignored inside a work tree.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." 2>/dev/null && pwd || echo "")"

VISIBILITY_REGISTRY="${SCRIPT_DIR}/repository_visibility.py"
BASE_CONFIG="${TRACTION_CONTROL_BASE_REPOS_CONFIG:-${REPO_ROOT}/config/traction-control-agents/repos.conf}"
PRIVATE_CONFIG="${TRACTION_CONTROL_PRIVATE_REPOS_BUNDLE:-${XDG_CONFIG_HOME:-${HOME}/.config}/traction-control/private-repos.local.conf}"
PRIVATE_REGISTRY="${TRACTION_CONTROL_PRIVATE_REPOS_CONFIG:-${REPO_ROOT}/config/repository-visibility/private.local.json}"
PUBLIC_REGISTRY="${TRACTION_CONTROL_PUBLIC_REPOS_CONFIG:-${REPO_ROOT}/config/repository-visibility/public.local.json}"
OUTPUT="${TRACTION_CONTROL_LOCAL_REPOS_CONFIG:-${XDG_CONFIG_HOME:-${HOME}/.config}/traction-control/repos.local.conf}"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"

DRY_RUN=0
LIST=0
INIT=0

usage() {
  cat <<'EOF'
Usage: render_private_bundle_overlay.sh [options]

Merge the tracked public support bundle with the ignored private-repository
overlay and render an owner-only local bundle for --repo-config.

Options:
  --init                  Write a starter overlay listing every private
                          registry repository as a commented-out candidate.
                          Candidates stay unenrolled until you uncomment them.
  --list                  Print merged bundle membership per profile and exit.
  --dry-run               Validate and report without writing the output.
  --base-config PATH      Tracked public base bundle.
  --private-config PATH   Ignored private overlay data file.
  --output PATH           Rendered owner-only local bundle path.
  --portfolio-root PATH   Portfolio root used to locate checkouts for --init.
  --help                  Show this help text.

The overlay uses the same pipe-delimited shape as the tracked base bundle:

  profiles|name|github_slug|relative_path|purpose

Every overlay slug must be classified private in the visibility registry.
Public slugs belong in the tracked base bundle instead.
EOF
}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
info() { printf 'info: %s\n' "$*"; }
plan() { printf 'plan: %s\n' "$*"; }

need_value() { [[ $# -ge 2 ]] || fail "$1 requires a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init) INIT=1 ;;
    --list) LIST=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --base-config) need_value "$@"; BASE_CONFIG="$2"; shift ;;
    --private-config) need_value "$@"; PRIVATE_CONFIG="$2"; shift ;;
    --output) need_value "$@"; OUTPUT="$2"; shift ;;
    --portfolio-root) need_value "$@"; PORTFOLIO_ROOT="$2"; shift ;;
    --help) usage; exit 0 ;;
    *) usage >&2; fail "unknown option: $1" ;;
  esac
  shift
done

[[ -f "${BASE_CONFIG}" ]] || fail "tracked base bundle not found: ${BASE_CONFIG}"
[[ -f "${VISIBILITY_REGISTRY}" ]] || fail "visibility registry tool not found: ${VISIBILITY_REGISTRY}"

# An overlay that Git tracks would publish private repository names on the next
# push, and an untracked-but-unignored one is a single `git add -A` away from
# the same leak. The default location lives outside any repository; when an
# operator points --private-config inside a work tree, require it to be ignored.
assert_not_publishable() {
  local path="$1"
  local dir
  dir="$(cd "$(dirname "${path}")" && pwd)"
  git -C "${dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  if git -C "${dir}" ls-files --error-unmatch "${path}" >/dev/null 2>&1; then
    fail "private overlay must not be tracked by Git: ${path}"
  fi
  if ! git -C "${dir}" check-ignore -q "${path}" 2>/dev/null; then
    fail "private overlay lives in a Git work tree but is not ignored: ${path}
add an ignore rule for it, or keep it at the default location outside the repository"
  fi
}

if (( INIT == 1 )); then
  [[ -f "${PRIVATE_REGISTRY}" ]] || fail "private registry not found: ${PRIVATE_REGISTRY}"
  [[ ! -e "${PRIVATE_CONFIG}" ]] || fail "private overlay already exists: ${PRIVATE_CONFIG}"
  assert_not_publishable "${PRIVATE_CONFIG}"
  umask 077
  mkdir -p "$(dirname "${PRIVATE_CONFIG}")"
  PORTFOLIO_ROOT="${PORTFOLIO_ROOT}" python3 - "${PRIVATE_REGISTRY}" "${PRIVATE_CONFIG}" <<'PY'
import json
import os
import sys
from pathlib import Path

registry_path, out_path = sys.argv[1], sys.argv[2]
with open(registry_path, encoding="utf-8") as handle:
    registry = json.load(handle)

root = os.environ.get("PORTFOLIO_ROOT", "")
lines = [
    "# Ignored private-repository support bundle overlay.",
    "# Rendered into the local merged bundle by render_private_bundle_overlay.sh.",
    "#",
    "# Every entry must be classified private in the visibility registry.",
    "# Candidates below are unenrolled: uncomment a line and set its profiles",
    "# to enroll that repository in a deployment tier.",
    "#",
    "# profiles|name|github_slug|relative_path|purpose",
    "",
]
for entry in sorted(registry.get("repositories", []), key=lambda item: item.get("slug", "")):
    slug = entry.get("slug", "")
    if "/" not in slug:
        continue
    name = slug.split("/", 1)[1]
    relative = f"util-repos/{name}"
    if root:
        for candidate in (f"util-repos/{name}", f"sec-repos/{name}", f"doc-repos/{name}", name):
            if (Path(root) / candidate / ".git").exists():
                relative = candidate
                break
    lines.append(f"# light,moderate,heavy|{name}|{slug}|{relative}|TODO describe this private support repo")

Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
  chmod 600 "${PRIVATE_CONFIG}"
  info "wrote starter private overlay: ${PRIVATE_CONFIG}"
  info "every candidate is commented out; uncomment the ones to enroll, then rerun without --init"
  exit 0
fi

if [[ ! -f "${PRIVATE_CONFIG}" ]]; then
  fail "private overlay not found: ${PRIVATE_CONFIG}
create one with: bash ${BASH_SOURCE[0]##*/} --init
or copy the tracked template: config/traction-control-agents/private-repos.example.conf"
fi
assert_not_publishable "${PRIVATE_CONFIG}"

for registry in "${PRIVATE_REGISTRY}" "${PUBLIC_REGISTRY}"; do
  [[ -f "${registry}" ]] || fail "visibility registry not found: ${registry}"
done

declare -a OVERLAY_LINES=()
declare -a BASE_NAMES=()

while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" || "${line}" == \#* ]] && continue
  IFS='|' read -r _profiles name _rest <<<"${line}"
  [[ -n "${name}" ]] && BASE_NAMES+=("${name}")
done < "${BASE_CONFIG}"

line_number=0
while IFS= read -r line || [[ -n "${line}" ]]; do
  line_number=$((line_number + 1))
  [[ -z "${line}" || "${line}" == \#* ]] && continue

  IFS='|' read -r profiles name slug relative purpose extra <<<"${line}"
  [[ -z "${extra:-}" ]] || fail "line ${line_number}: expected 5 pipe-delimited fields"
  for field_name in profiles name slug relative purpose; do
    [[ -n "${!field_name}" ]] || fail "line ${line_number}: empty ${field_name} field"
  done

  IFS=',' read -r -a profile_list <<<"${profiles}"
  for profile in "${profile_list[@]}"; do
    case "${profile}" in
      light|moderate|heavy) ;;
      *) fail "line ${line_number}: invalid profile '${profile}' (expected light, moderate, or heavy)" ;;
    esac
  done

  [[ "${slug}" == */* ]] || fail "line ${line_number}: slug must be OWNER/REPO: ${slug}"

  for existing in ${BASE_NAMES[@]+"${BASE_NAMES[@]}"}; do
    [[ "${existing}" != "${name}" ]] ||
      fail "line ${line_number}: '${name}' is already in the tracked base bundle"
  done
  for existing in ${OVERLAY_LINES[@]+"${OVERLAY_LINES[@]}"}; do
    IFS='|' read -r _p existing_name _r <<<"${existing}"
    [[ "${existing_name}" != "${name}" ]] || fail "line ${line_number}: duplicate overlay entry '${name}'"
  done

  classification="$(python3 "${VISIBILITY_REGISTRY}" classify \
    --private "${PRIVATE_REGISTRY}" \
    --public "${PUBLIC_REGISTRY}" \
    --slug "${slug}")"
  case "${classification}" in
    private) ;;
    public)
      fail "line ${line_number}: ${slug} is classified public; add it to the tracked base bundle instead" ;;
    *)
      fail "line ${line_number}: ${slug} is ${classification} in the visibility registry; record it first with scripts/create_private_github_repo.sh" ;;
  esac

  OVERLAY_LINES+=("${line}")
  info "verified private support repo: ${name} (${slug}) -> ${profiles}"
done < "${PRIVATE_CONFIG}"

(( ${#OVERLAY_LINES[@]} > 0 )) || fail "private overlay enrolled no repositories: ${PRIVATE_CONFIG}"

if (( LIST == 1 )); then
  for profile in light moderate heavy; do
    printf '%s:\n' "${profile}"
    while IFS= read -r line || [[ -n "${line}" ]]; do
      [[ -z "${line}" || "${line}" == \#* ]] && continue
      IFS='|' read -r profiles name slug _rest <<<"${line}"
      case ",${profiles}," in
        *",${profile},"*) printf '  %s (%s) [tracked]\n' "${name}" "${slug}" ;;
      esac
    done < "${BASE_CONFIG}"
    for line in "${OVERLAY_LINES[@]}"; do
      IFS='|' read -r profiles name slug _rest <<<"${line}"
      case ",${profiles}," in
        *",${profile},"*) printf '  %s (%s) [private overlay]\n' "${name}" "${slug}" ;;
      esac
    done
  done
  exit 0
fi

if (( DRY_RUN == 1 )); then
  plan "render ${#OVERLAY_LINES[@]} private overlay entries onto ${BASE_CONFIG}"
  plan "write owner-only merged bundle: ${OUTPUT}"
  exit 0
fi

umask 077
mkdir -p "$(dirname "${OUTPUT}")"
{
  cat "${BASE_CONFIG}"
  printf '# --- private overlay (rendered; do not edit or commit) ---\n'
  for line in "${OVERLAY_LINES[@]}"; do
    printf '%s\n' "${line}"
  done
} > "${OUTPUT}"
chmod 600 "${OUTPUT}"

info "rendered merged bundle: ${OUTPUT}"
info "private entries: ${#OVERLAY_LINES[@]}"
cat <<EOF

Install a profile with the merged bundle. Private repositories need SSH or an
authenticated HTTPS credential helper on machines where they are not already
checked out:

  bash ${REPO_ROOT}/scripts/install_traction_control_agents.sh \\
    --tier <light|moderate|heavy> \\
    --repo-config ${OUTPUT} \\
    --clone-protocol ssh
EOF
