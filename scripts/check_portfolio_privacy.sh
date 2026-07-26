#!/usr/bin/env bash
# Fast index gate for local portfolio metadata and private repository names.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PRIVATE_REGISTRY="${TRACTION_CONTROL_PRIVATE_REPOS_CONFIG:-${REPO_ROOT}/config/repository-visibility/private.local.json}"
PUBLIC_REGISTRY="${TRACTION_CONTROL_PUBLIC_REPOS_CONFIG:-${REPO_ROOT}/config/repository-visibility/public.local.json}"

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
    'config/secret-scan/*.local.txt'
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
else
  printf 'private registry unavailable; operational-path index gate passed\n'
fi
