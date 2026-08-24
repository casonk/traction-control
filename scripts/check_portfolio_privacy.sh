#!/usr/bin/env bash
# Fast index gate for local portfolio metadata and private repository names.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# The registry is gitignored, so it exists only in the main checkout — never in
# a linked worktree. Resolving it relative to REPO_ROOT alone meant every run
# inside a worktree found no registry, skipped the disclosure audit, and
# reported a pass it had not performed. Resolve the main checkout explicitly so
# a worktree audits against the same registry the main checkout would.
REGISTRY_ROOT="${REPO_ROOT}"
git_common_dir="$(git -C "${REPO_ROOT}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "${git_common_dir}" ]]; then
  main_checkout="$(dirname "${git_common_dir}")"
  if [[ "${main_checkout}" != "${REPO_ROOT}" && -d "${main_checkout}/config/repository-visibility" ]]; then
    REGISTRY_ROOT="${main_checkout}"
  fi
fi

PRIVATE_REGISTRY="${TRACTION_CONTROL_PRIVATE_REPOS_CONFIG:-${REGISTRY_ROOT}/config/repository-visibility/private.local.json}"
PUBLIC_REGISTRY="${TRACTION_CONTROL_PUBLIC_REPOS_CONFIG:-${REGISTRY_ROOT}/config/repository-visibility/public.local.json}"

cd "${REPO_ROOT}"

tracked_operational="$(
  git ls-files --cached -- \
    'config/repository-visibility/*.local.json' \
    'config/repository-visibility/.repository-visibility.lock' \
    'config/repository-visibility/.*.tmp.local.json' \
    'config/portfolio/*.local.json' \
    'config/portfolio/.portfolio-materializer.lock' \
    'config/portfolio/.*.tmp.local.json' \
    'config/portfolio-sidecar/*.local.*' \
    'config/portfolio-sidecar/.portfolio-sidecar.lock' \
    'config/portfolio-sidecar/.*.tmp.local.*' \
    'config/portfolio-sidecar/credentials/**' \
    'config/portfolio-sidecar/spool/**' \
    'config/portfolio-sidecar/state/**' \
    'config/secret-scan/*.local.txt' \
    'config/air-primary.local.toml'
)"
if [[ -n "${tracked_operational}" ]]; then
  printf 'ERROR: operational private metadata is staged or tracked:\n%s\n' \
    "${tracked_operational}" >&2
  exit 1
fi

if [[ -e "${PRIVATE_REGISTRY}" || -e "${PUBLIC_REGISTRY}" ]]; then
  if [[ ! -f "${PRIVATE_REGISTRY}" || ! -f "${PUBLIC_REGISTRY}" ]]; then
    printf 'ERROR: both private and public registry files are required for disclosure audit\n' >&2
    exit 1
  fi
  PYTHONDONTWRITEBYTECODE=1 python3 scripts/repository_visibility.py \
    audit-private-disclosures \
    --private "${PRIVATE_REGISTRY}" \
    --public "${PUBLIC_REGISTRY}" \
    --root "${REPO_ROOT}"
elif [[ "${TRACTION_CONTROL_REQUIRE_PRIVACY_AUDIT:-0}" == "1" ]]; then
  printf 'ERROR: private registry unavailable and TRACTION_CONTROL_REQUIRE_PRIVACY_AUDIT=1; refusing to skip the disclosure audit\n' >&2
  printf '  looked for: %s\n' "${PRIVATE_REGISTRY}" >&2
  exit 1
else
  # Fresh clones and CI legitimately have no registry, so this cannot fail
  # closed unconditionally. Say plainly that the audit was SKIPPED — the
  # previous wording ("gate passed") described a check that never ran, which
  # is how private repository names reached a tracked file unnoticed.
  printf 'WARNING: private registry unavailable at %s\n' "${PRIVATE_REGISTRY}" >&2
  printf 'WARNING: disclosure audit SKIPPED — this run proves nothing about private-name disclosure\n' >&2
  printf 'WARNING: re-run from the main checkout, or set TRACTION_CONTROL_PRIVATE_REPOS_CONFIG/_PUBLIC_REPOS_CONFIG, before trusting a clean result\n' >&2
fi
