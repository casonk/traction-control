#!/usr/bin/env bash
# Aggregate CI gate for the fail-closed repository visibility registry.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REGISTRY_DIR="${REPO_ROOT}/config/repository-visibility"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/repository-visibility-ci.XXXXXX")"

cleanup() {
  case "${TEST_ROOT}" in
    "${TMPDIR:-/tmp}"/repository-visibility-ci.*) rm -rf "${TEST_ROOT}" ;;
  esac
}
trap cleanup EXIT HUP INT TERM

cd "${REPO_ROOT}"

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests \
  -p 'test_repository_visibility.py' \
  -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests \
  -p 'test_portfolio_*.py' \
  -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests \
  -p 'test_render_air_primary.py' \
  -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests \
  -p 'test_clockwork_launchd_contract.py' \
  -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests \
  -p 'test_run_traction_control_job.py' \
  -v
bash tests/test_create_private_github_repo.sh

git check-ignore --quiet --no-index \
  "${REGISTRY_DIR}/private.local.json"
git check-ignore --quiet --no-index \
  "${REGISTRY_DIR}/public.local.json"
git check-ignore --quiet --no-index \
  "${REGISTRY_DIR}/.repository-visibility.lock"
git check-ignore --quiet --no-index \
  "${REGISTRY_DIR}/.private.local.json.crash.tmp.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio/portfolio.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio/lifecycle-review.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio/lifecycle-review-plan.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio/.portfolio-materializer.lock"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio/.review.local.json.crash.tmp.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/policy.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/targets.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/state.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/.portfolio-sidecar.lock"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/.state.local.json.crash.tmp.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/credentials/restic-password"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/spool/pending.bin"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/state/authority.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/podman-mesh.local.json"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/portfolio-sidecar/quadlets.local.d/target.container"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/secret-scan/repositories.local.txt"
git check-ignore --quiet --no-index \
  "${REPO_ROOT}/config/air-primary.local.toml"

TRACKED_OPERATIONAL_FILES="$(
  git ls-files -- \
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
if [[ -n "${TRACKED_OPERATIONAL_FILES}" ]]; then
  printf 'operational registry files must not be tracked:\n%s\n' \
    "${TRACKED_OPERATIONAL_FILES}" >&2
  exit 1
fi

cp "${REGISTRY_DIR}/private.example.json" \
  "${TEST_ROOT}/private.local.json"
cp "${REGISTRY_DIR}/public.example.json" \
  "${TEST_ROOT}/public.local.json"
cp "${REPO_ROOT}/config/portfolio/portfolio.example.json" \
  "${TEST_ROOT}/portfolio.local.json"
chmod 0600 \
  "${TEST_ROOT}/private.local.json" \
  "${TEST_ROOT}/public.local.json" \
  "${TEST_ROOT}/portfolio.local.json"

python3 scripts/repository_visibility.py validate \
  --private "${TEST_ROOT}/private.local.json" \
  --public "${TEST_ROOT}/public.local.json"
python3 scripts/portfolio_materializer.py validate \
  --private "${TEST_ROOT}/private.local.json" \
  --public "${TEST_ROOT}/public.local.json" \
  --catalog "${TEST_ROOT}/portfolio.local.json" \
  --portfolio-root "${TEST_ROOT}"
