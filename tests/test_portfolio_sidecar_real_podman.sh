#!/usr/bin/env bash
# Run real Restic backup and restore-drill flows against four isolated SFTP targets.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COORDINATOR_CONTAINERFILE="${REPO_ROOT}/tests/containers/SidecarRealCoordinator.Containerfile"
TARGET_CONTAINERFILE="${REPO_ROOT}/tests/containers/SidecarRealSftp.Containerfile"
COORDINATOR_IMAGE="${TRACTION_CONTROL_SIDECAR_REAL_IMAGE:-traction-control-sidecar-real-test:local}"
TARGET_IMAGE="${TRACTION_CONTROL_SIDECAR_SFTP_IMAGE:-traction-control-sidecar-sftp-test:local}"
PODMAN_CONNECTION="${TRACTION_CONTROL_PODMAN_CONNECTION:-}"
CONTEXT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/traction-control-sidecar-real-context.XXXXXX")"
RUN_TOKEN="$$"
NETWORK="tc-sidecar-real-${RUN_TOKEN}"
TEST_STATE_VOLUME="tc-sidecar-state-${RUN_TOKEN}"
COORDINATOR_CONTAINER="tc-sidecar-coordinator-${RUN_TOKEN}"
THIRD_OCTET="$((RUN_TOKEN % 180 + 40))"
SUBNET="10.203.${THIRD_OCTET}.0/24"
HOSTED_ADDRESS="10.203.${THIRD_OCTET}.10"
MESH_1_ADDRESS="10.203.${THIRD_OCTET}.21"
MESH_2_ADDRESS="10.203.${THIRD_OCTET}.22"
MESH_3_ADDRESS="10.203.${THIRD_OCTET}.23"
HOSTED_CONTAINER="tc-sidecar-hosted-${RUN_TOKEN}"
MESH_1_CONTAINER="tc-sidecar-mesh-1-${RUN_TOKEN}"
MESH_2_CONTAINER="tc-sidecar-mesh-2-${RUN_TOKEN}"
MESH_3_CONTAINER="tc-sidecar-mesh-3-${RUN_TOKEN}"
TARGET_VOLUMES=(
  "tc-sidecar-hosted-repo-${RUN_TOKEN}"
  "tc-sidecar-mesh-1-repo-${RUN_TOKEN}"
  "tc-sidecar-mesh-2-repo-${RUN_TOKEN}"
  "tc-sidecar-mesh-3-repo-${RUN_TOKEN}"
)
CREATED_NETWORK=0
FAILURE_LOGS_SHOWN=0
CREATED_CONTAINERS=(
  "${COORDINATOR_CONTAINER}"
)
CREATED_VOLUMES=()

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

show_target_logs() {
  local container
  [[ "${FAILURE_LOGS_SHOWN}" -eq 0 ]] || return 0
  FAILURE_LOGS_SHOWN=1
  for container in \
    "${HOSTED_CONTAINER}" \
    "${MESH_1_CONTAINER}" \
    "${MESH_2_CONTAINER}" \
    "${MESH_3_CONTAINER}"; do
    printf 'Target log: %s\n' "${container}" >&2
    "${PODMAN[@]}" logs "${container}" 2>/dev/null || true
  done
}

PODMAN=(podman)
if [[ -z "${PODMAN_CONNECTION}" && "$(uname -s)" == "Darwin" ]]; then
  PODMAN_CONNECTION="podman-machine-default"
fi
if [[ -n "${PODMAN_CONNECTION}" ]]; then
  PODMAN+=(--connection "${PODMAN_CONNECTION}")
fi

cleanup() {
  local original_status="$?"
  local index
  if [[ "${original_status}" -ne 0 ]]; then
    show_target_logs
  fi
  for ((index=${#CREATED_CONTAINERS[@]} - 1; index >= 0; index--)); do
    "${PODMAN[@]}" rm --force "${CREATED_CONTAINERS[index]}" >/dev/null 2>&1 || true
  done
  if [[ "${CREATED_NETWORK}" -eq 1 ]]; then
    "${PODMAN[@]}" network rm --force "${NETWORK}" >/dev/null 2>&1 || true
  fi
  for ((index=${#CREATED_VOLUMES[@]} - 1; index >= 0; index--)); do
    "${PODMAN[@]}" volume rm --force "${CREATED_VOLUMES[index]}" >/dev/null 2>&1 || true
  done
  case "${CONTEXT_ROOT}" in
    "${TMPDIR:-/tmp}"/traction-control-sidecar-real-context.*)
      rm -rf "${CONTEXT_ROOT}"
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM

command -v podman >/dev/null 2>&1 \
  || fail "Podman is required; run scripts/install_podman_runtime.sh"
"${PODMAN[@]}" version >/dev/null 2>&1 \
  || fail "Podman is installed but the selected connection is unavailable"

for required in \
  "${COORDINATOR_CONTAINERFILE}" \
  "${TARGET_CONTAINERFILE}" \
  "${REPO_ROOT}/tests/containers/sidecar-real/sshd_config" \
  "${REPO_ROOT}/tests/containers/sidecar-real/start-sftp-target.sh" \
  "${REPO_ROOT}/tests/containers/sidecar-real/run-real-sidecar-test.sh"; do
  [[ -f "${required}" ]] || fail "required test source is missing: ${required}"
done

mkdir -p \
  "${CONTEXT_ROOT}/scripts" \
  "${CONTEXT_ROOT}/sidecar-real"
for source_name in \
  portfolio_materializer.py \
  portfolio_sidecar.py \
  repository_visibility.py; do
  cp -p \
    "${REPO_ROOT}/scripts/${source_name}" \
    "${CONTEXT_ROOT}/scripts/${source_name}"
done
cp -p \
  "${REPO_ROOT}/tests/containers/sidecar-real/sshd_config" \
  "${REPO_ROOT}/tests/containers/sidecar-real/start-sftp-target.sh" \
  "${REPO_ROOT}/tests/containers/sidecar-real/run-real-sidecar-test.sh" \
  "${CONTEXT_ROOT}/sidecar-real/"

printf 'Building real sidecar coordinator and SFTP target images with %s...\n' \
  "${PODMAN[*]}"
"${PODMAN[@]}" build \
  --file "${COORDINATOR_CONTAINERFILE}" \
  --tag "${COORDINATOR_IMAGE}" \
  "${CONTEXT_ROOT}"
"${PODMAN[@]}" build \
  --file "${TARGET_CONTAINERFILE}" \
  --tag "${TARGET_IMAGE}" \
  "${CONTEXT_ROOT}"

common_coordinator_arguments=(
  --init
  --read-only
  --cap-drop all
  --security-opt no-new-privileges
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m
  --volume "${TEST_STATE_VOLUME}:/test:rw"
  --env "SIDECAR_HOSTED_HOST=${HOSTED_CONTAINER}"
  --env "SIDECAR_MESH_1_ADDRESS=${MESH_1_ADDRESS}"
  --env "SIDECAR_MESH_2_ADDRESS=${MESH_2_ADDRESS}"
  --env "SIDECAR_MESH_3_ADDRESS=${MESH_3_ADDRESS}"
)

"${PODMAN[@]}" volume create "${TEST_STATE_VOLUME}" >/dev/null
CREATED_VOLUMES+=("${TEST_STATE_VOLUME}")

"${PODMAN[@]}" network create \
  --internal \
  --subnet "${SUBNET}" \
  "${NETWORK}" >/dev/null
CREATED_NETWORK=1
[[ "$("${PODMAN[@]}" network inspect --format '{{.Internal}}' "${NETWORK}")" == "true" ]] \
  || fail "created Podman network is not internal"

"${PODMAN[@]}" run \
  --detach \
  "${common_coordinator_arguments[@]}" \
  --network "${NETWORK}" \
  --name "${COORDINATOR_CONTAINER}" \
  --entrypoint /bin/sleep \
  "${COORDINATOR_IMAGE}" \
  infinity >/dev/null
"${PODMAN[@]}" exec \
  "${COORDINATOR_CONTAINER}" \
  /usr/local/bin/run-real-sidecar-test \
  generate-keys

for volume in "${TARGET_VOLUMES[@]}"; do
  "${PODMAN[@]}" volume create "${volume}" >/dev/null
  CREATED_VOLUMES+=("${volume}")
done

start_target() {
  local name="$1"
  local address="$2"
  local key_prefix="$3"
  local volume="$4"
  "${PODMAN[@]}" run \
    --detach \
    --read-only \
    --tmpfs /run:rw,nosuid,nodev,size=16m \
    --tmpfs /tmp:rw,nosuid,nodev,size=16m \
    --security-opt no-new-privileges \
    --network "${NETWORK}" \
    --ip "${address}" \
    --name "${name}" \
    --env "SIDECAR_TARGET_KEY_PREFIX=${key_prefix}" \
    --volume "${TEST_STATE_VOLUME}:/test-fixture:ro" \
    --volume "${volume}:/home/sidecarbackup/repository:rw" \
    "${TARGET_IMAGE}" >/dev/null
  CREATED_CONTAINERS+=("${name}")
}

start_target \
  "${HOSTED_CONTAINER}" "${HOSTED_ADDRESS}" hosted "${TARGET_VOLUMES[0]}"
start_target \
  "${MESH_1_CONTAINER}" "${MESH_1_ADDRESS}" mesh_1 "${TARGET_VOLUMES[1]}"
start_target \
  "${MESH_2_CONTAINER}" "${MESH_2_ADDRESS}" mesh_2 "${TARGET_VOLUMES[2]}"
start_target \
  "${MESH_3_CONTAINER}" "${MESH_3_ADDRESS}" mesh_3 "${TARGET_VOLUMES[3]}"

printf '\nRunning real L2 and three-replica L3 backup/drill...\n'
set +e
"${PODMAN[@]}" exec \
  "${COORDINATOR_CONTAINER}" \
  /usr/local/bin/run-real-sidecar-test \
  baseline
BASELINE_STATUS=$?
set -e
if [[ "${BASELINE_STATUS}" -ne 0 ]]; then
  show_target_logs
  exit "${BASELINE_STATUS}"
fi

printf '\nStopping one mesh target and verifying strict-majority degradation...\n'
"${PODMAN[@]}" stop --time 2 "${MESH_3_CONTAINER}" >/dev/null
"${PODMAN[@]}" exec \
  "${COORDINATOR_CONTAINER}" \
  /usr/local/bin/run-real-sidecar-test \
  outage

printf '\nReal isolated SFTP/Restic sidecar regressions passed.\n'
