#!/usr/bin/env bash
# Build a narrow Linux test image and run each requested bootstrap tier in its
# own networkless, read-only container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="${TRACTION_CONTROL_WORKSPACE_ROOT:-$(cd "${REPO_ROOT}/../.." && pwd)}"
CLOCKWORK_REPO="${TRACTION_CONTROL_CLOCKWORK_REPO:-${WORKSPACE_ROOT}/util-repos/clockwork}"
ARCHILITY_REPO="${TRACTION_CONTROL_ARCHILITY_REPO:-${WORKSPACE_ROOT}/util-repos/archility}"
CONTAINERFILE="${REPO_ROOT}/tests/containers/Containerfile"
CONTAINER_TEST="${REPO_ROOT}/tests/containers/test-tier-install.sh"
IMAGE_TAG="${TRACTION_CONTROL_CONTAINER_IMAGE:-traction-control-installer-test:local}"
CONTEXT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/traction-control-container-context.XXXXXX")"
ENGINE="${CONTAINER_ENGINE:-}"
PODMAN_CONNECTION="${TRACTION_CONTROL_PODMAN_CONNECTION:-}"

cleanup() {
  case "${CONTEXT_ROOT}" in
    "${TMPDIR:-/tmp}"/traction-control-container-context.*)
      rm -rf "${CONTEXT_ROOT}"
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

if [[ -z "${ENGINE}" ]]; then
  for engine_candidate in podman docker; do
    if command -v "${engine_candidate}" >/dev/null 2>&1; then
      ENGINE="${engine_candidate}"
      break
    fi
  done
fi
[[ -n "${ENGINE}" ]] \
  || fail "no container engine found; run scripts/install_podman_runtime.sh or install Docker/Colima"
command -v "${ENGINE}" >/dev/null 2>&1 \
  || fail "container engine is not executable: ${ENGINE}"
case "$(basename "${ENGINE}")" in
  podman|docker) ;;
  *) fail "container engine must be Podman or Docker: ${ENGINE}" ;;
esac
ENGINE_COMMAND=("${ENGINE}")
if [[ "$(basename "${ENGINE}")" == "podman" ]]; then
  if [[ -z "${PODMAN_CONNECTION}" && "$(uname -s)" == "Darwin" ]]; then
    PODMAN_CONNECTION="podman-machine-default"
  fi
  if [[ -n "${PODMAN_CONNECTION}" ]]; then
    ENGINE_COMMAND+=(--connection "${PODMAN_CONNECTION}")
  fi
fi
"${ENGINE_COMMAND[@]}" version >/dev/null 2>&1 \
  || fail "container engine is installed but unavailable: ${ENGINE}"

[[ -f "${CONTAINERFILE}" ]] || fail "missing Containerfile: ${CONTAINERFILE}"
[[ -f "${CONTAINER_TEST}" ]] || fail "missing container test: ${CONTAINER_TEST}"
[[ -d "${CLOCKWORK_REPO}/src/clockwork" ]] \
  || fail "Clockwork source not found: ${CLOCKWORK_REPO}/src/clockwork"
[[ -d "${ARCHILITY_REPO}/src/archility" ]] \
  || fail "Archility source not found: ${ARCHILITY_REPO}/src/archility"

TIERS=(light moderate heavy)
if (( $# > 0 )); then
  TIERS=()
  for requested_tier in "$@"; do
    case "${requested_tier}" in
      light|moderate|heavy) TIERS+=("${requested_tier}") ;;
      *) fail "unsupported tier: ${requested_tier}" ;;
    esac
  done
fi

mkdir -p \
  "${CONTEXT_ROOT}/repo-sources/traction-control/scripts/lib" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/config" \
  "${CONTEXT_ROOT}/repo-sources/clockwork" \
  "${CONTEXT_ROOT}/repo-sources/archility"

TRACTION_SCRIPTS=(
  install_traction_control_agents.sh
  portfolio-audit.sh
  bug_sweep_agentic.sh
  ci_repair_agentic.sh
  ci_repair_agentic_repair.sh
  archility-daily.sh
  archility-weekly.sh
  template_consolidation_agentic.sh
  refs_audit_agentic.sh
  tachometer_disk_pressure_agentic.sh
  install_portfolio_audit_systemd.sh
  install_bug_sweep_agentic_systemd.sh
  install_ci_repair_agentic_discovery_systemd.sh
  install_ci_repair_agentic_repair_systemd.sh
  install_ci_repair_agentic_systemd.sh
  install_archility_daily_systemd.sh
  install_archility_weekly_systemd.sh
  install_template_consolidation_agentic_systemd.sh
  install_refs_audit_agentic_systemd.sh
  install_tachometer_disk_pressure_agentic_systemd.sh
)

for script_name in "${TRACTION_SCRIPTS[@]}"; do
  [[ -f "${REPO_ROOT}/scripts/${script_name}" ]] \
    || fail "required script not found: ${script_name}"
  cp -p \
    "${REPO_ROOT}/scripts/${script_name}" \
    "${CONTEXT_ROOT}/repo-sources/traction-control/scripts/${script_name}"
done
cp -p \
  "${REPO_ROOT}/scripts/lib/agentic_provider.sh" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/scripts/lib/agentic_provider.sh"
cp -R \
  "${REPO_ROOT}/config/clockwork" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/config/clockwork"
cp -R \
  "${REPO_ROOT}/config/traction-control-agents" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/config/traction-control-agents"
cp -R \
  "${CLOCKWORK_REPO}/src" \
  "${CONTEXT_ROOT}/repo-sources/clockwork/src"
cp -R \
  "${ARCHILITY_REPO}/src" \
  "${CONTEXT_ROOT}/repo-sources/archility/src"

for repository_name in tachometer auto-pass shock-relay; do
  mkdir -p "${CONTEXT_ROOT}/repo-sources/${repository_name}"
  printf '%s container fixture\n' "${repository_name}" \
    > "${CONTEXT_ROOT}/repo-sources/${repository_name}/README.md"
done
cp -p "${CONTAINER_TEST}" "${CONTEXT_ROOT}/test-tier-install.sh"

printf 'Building %s with %s...\n' "${IMAGE_TAG}" "${ENGINE_COMMAND[*]}"
"${ENGINE_COMMAND[@]}" build \
  --file "${CONTAINERFILE}" \
  --tag "${IMAGE_TAG}" \
  "${CONTEXT_ROOT}"

for tier in "${TIERS[@]}"; do
  printf '\nRunning %s install in a fresh networkless container...\n' "${tier}"
  "${ENGINE_COMMAND[@]}" run \
    --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=512m \
    --env "TRACTION_CONTROL_TEST_TIER=${tier}" \
    --name "traction-control-install-${tier}-$$" \
    "${IMAGE_TAG}"
done

printf '\nAll requested container installs passed: %s\n' "${TIERS[*]}"
