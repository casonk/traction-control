#!/usr/bin/env bash
# Smoke-test the production SFTP target image with disposable Podman resources.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_ROOT="${REPO_ROOT}/containers/portfolio-sidecar-sftp"
PODMAN_CONNECTION="${TRACTION_CONTROL_PODMAN_CONNECTION:-}"
RUN_TOKEN="$$"
IMAGE="localhost/traction-control-sidecar-sftp-smoke-${RUN_TOKEN}:local"
CONTAINER="tc-sidecar-sftp-smoke-${RUN_TOKEN}"
NETWORK="tc-sidecar-sftp-smoke-${RUN_TOKEN}"
VOLUME="tc-sidecar-sftp-smoke-${RUN_TOKEN}"
HOST_SECRET="tc-sidecar-host-key-${RUN_TOKEN}"
AUTHORIZED_SECRET="tc-sidecar-authorized-keys-${RUN_TOKEN}"
FIXTURE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/traction-control-sftp-smoke.XXXXXX")"
CREATED_IMAGE=0
CREATED_CONTAINER=0
CREATED_NETWORK=0
CREATED_VOLUME=0
CREATED_HOST_SECRET=0
CREATED_AUTHORIZED_SECRET=0

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

PODMAN=(podman)
if [[ -z "${PODMAN_CONNECTION}" && "$(uname -s)" == "Darwin" ]]; then
  PODMAN_CONNECTION="podman-machine-default"
fi
if [[ -n "${PODMAN_CONNECTION}" ]]; then
  PODMAN+=(--connection "${PODMAN_CONNECTION}")
fi

start_target() {
  local publish_spec="$1"

  "${PODMAN[@]}" run \
    --detach \
    --name "${CONTAINER}" \
    --network "${NETWORK}" \
    --read-only \
    --tmpfs /run:rw,nosuid,nodev,size=16m \
    --tmpfs /tmp:rw,nosuid,nodev,size=16m \
    --security-opt no-new-privileges \
    --cap-drop all \
    --cap-add CHOWN \
    --cap-add DAC_OVERRIDE \
    --cap-add SETGID \
    --cap-add SETUID \
    --cap-add SYS_CHROOT \
    --env SIDECAR_SFTP_PORT=2222 \
    --secret "${HOST_SECRET},target=sidecar-host-key,type=mount,mode=400" \
    --secret "${AUTHORIZED_SECRET},target=sidecar-authorized-keys,type=mount,mode=400" \
    --volume "${VOLUME}:/srv/portfolio-sidecar/repository:rw,nodev,nosuid,noexec" \
    --publish "${publish_spec}" \
    "${IMAGE}" >/dev/null
  CREATED_CONTAINER=1
}

wait_for_health() {
  local ready=0

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if "${PODMAN[@]}" inspect --format '{{.State.Health.Status}}' "${CONTAINER}" \
      | grep -qx healthy; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "${ready}" -ne 1 ]]; then
    "${PODMAN[@]}" exec "${CONTAINER}" \
      /usr/local/bin/sftp-target-healthcheck >&2 || true
    fail "SFTP target did not become healthy"
  fi
}

configure_client_endpoint() {
  local published_endpoint

  published_endpoint="$("${PODMAN[@]}" port "${CONTAINER}" 2222/tcp | head -n 1)"
  [[ "${published_endpoint}" =~ ^127\.0\.0\.1:([0-9]+)$ ]] \
    || fail "could not resolve the loopback SFTP test port: ${published_endpoint}"
  published_port="${BASH_REMATCH[1]}"
  host_public_key="$(ssh-keygen -y -f "${FIXTURE_ROOT}/host_ed25519")"
  printf '[127.0.0.1]:%s %s\n' "${published_port}" "${host_public_key}" \
    >"${FIXTURE_ROOT}/known_hosts"
  sftp_common=(
    -q
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o PasswordAuthentication=no
    -o KbdInteractiveAuthentication=no
    -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=${FIXTURE_ROOT}/known_hosts"
    -i "${FIXTURE_ROOT}/client_ed25519"
    -P "${published_port}"
  )
}

cleanup() {
  local original_status="$?"
  if [[ "${original_status}" -ne 0 && "${CREATED_CONTAINER}" -eq 1 ]]; then
    "${PODMAN[@]}" logs "${CONTAINER}" >&2 || true
  fi
  if [[ "${CREATED_CONTAINER}" -eq 1 ]]; then
    "${PODMAN[@]}" rm --force "${CONTAINER}" >/dev/null 2>&1 || true
  fi
  if [[ "${CREATED_NETWORK}" -eq 1 ]]; then
    "${PODMAN[@]}" network rm --force "${NETWORK}" >/dev/null 2>&1 || true
  fi
  if [[ "${CREATED_VOLUME}" -eq 1 ]]; then
    "${PODMAN[@]}" volume rm --force "${VOLUME}" >/dev/null 2>&1 || true
  fi
  if [[ "${CREATED_HOST_SECRET}" -eq 1 ]]; then
    "${PODMAN[@]}" secret rm "${HOST_SECRET}" >/dev/null 2>&1 || true
  fi
  if [[ "${CREATED_AUTHORIZED_SECRET}" -eq 1 ]]; then
    "${PODMAN[@]}" secret rm "${AUTHORIZED_SECRET}" >/dev/null 2>&1 || true
  fi
  if [[ "${CREATED_IMAGE}" -eq 1 ]]; then
    "${PODMAN[@]}" image rm --force "${IMAGE}" >/dev/null 2>&1 || true
  fi
  case "${FIXTURE_ROOT}" in
    "${TMPDIR:-/tmp}"/traction-control-sftp-smoke.*)
      rm -rf "${FIXTURE_ROOT}"
      ;;
  esac
  exit "${original_status}"
}
trap cleanup EXIT HUP INT TERM

for command_name in cmp podman sftp ssh ssh-keygen; do
  command -v "${command_name}" >/dev/null 2>&1 \
    || fail "${command_name} is required"
done
for required_path in \
  "${IMAGE_ROOT}/Containerfile" \
  "${IMAGE_ROOT}/sshd_config" \
  "${IMAGE_ROOT}/start-sftp-target.sh" \
  "${IMAGE_ROOT}/sftp-target-healthcheck.sh"; do
  [[ -f "${required_path}" ]] || fail "required image source is missing"
done

"${PODMAN[@]}" info >/dev/null \
  || fail "the selected Podman connection is unavailable"

ssh-keygen -q -t ed25519 -N '' -C '' -f "${FIXTURE_ROOT}/host_ed25519"
ssh-keygen -q -t ed25519 -N '' -C '' -f "${FIXTURE_ROOT}/client_ed25519"
printf 'portable sidecar SFTP smoke payload\n' >"${FIXTURE_ROOT}/payload.txt"

"${PODMAN[@]}" build --format docker --tag "${IMAGE}" "${IMAGE_ROOT}"
CREATED_IMAGE=1
"${PODMAN[@]}" secret create "${HOST_SECRET}" \
  "${FIXTURE_ROOT}/host_ed25519" >/dev/null
CREATED_HOST_SECRET=1
"${PODMAN[@]}" secret create "${AUTHORIZED_SECRET}" \
  "${FIXTURE_ROOT}/client_ed25519.pub" >/dev/null
CREATED_AUTHORIZED_SECRET=1
"${PODMAN[@]}" network create "${NETWORK}" >/dev/null
CREATED_NETWORK=1
"${PODMAN[@]}" volume create "${VOLUME}" >/dev/null
CREATED_VOLUME=1

start_target '127.0.0.1::2222/tcp'
configure_client_endpoint
printf '%s\n' \
  'put payload.txt /repository/payload.txt' \
  'get /repository/payload.txt restored.txt' \
  'ls -la /repository' \
  >"${FIXTURE_ROOT}/sftp.batch"

wait_for_health

(
  cd "${FIXTURE_ROOT}"
  sftp "${sftp_common[@]}" \
    -b "${FIXTURE_ROOT}/sftp.batch" \
    sidecarbackup@127.0.0.1
)
cmp "${FIXTURE_ROOT}/payload.txt" "${FIXTURE_ROOT}/restored.txt" \
  || fail "SFTP round trip changed the payload"

if ssh \
  -o BatchMode=yes \
  -o PreferredAuthentications=password \
  -o PubkeyAuthentication=no \
  -o KbdInteractiveAuthentication=no \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${FIXTURE_ROOT}/known_hosts" \
  -p "${published_port}" \
  sidecarbackup@127.0.0.1 true >/dev/null 2>&1; then
  fail "password-only SSH authentication unexpectedly succeeded"
fi

ssh \
  -T \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=${FIXTURE_ROOT}/known_hosts" \
  -i "${FIXTURE_ROOT}/client_ed25519" \
  -p "${published_port}" \
  sidecarbackup@127.0.0.1 \
  'touch /repository/shell-command-ran' </dev/null >/dev/null 2>&1 || true
"${PODMAN[@]}" exec "${CONTAINER}" \
  test ! -e /srv/portfolio-sidecar/repository/shell-command-ran \
  || fail "a key-authenticated remote shell command escaped ForceCommand"

[[ "$("${PODMAN[@]}" inspect --format '{{.HostConfig.ReadonlyRootfs}}' "${CONTAINER}")" == "true" ]] \
  || fail "container root filesystem is not read-only"
[[ "$("${PODMAN[@]}" inspect --format '{{json .EffectiveCaps}}' "${CONTAINER}")" \
  == '["CAP_CHOWN","CAP_DAC_OVERRIDE","CAP_SETGID","CAP_SETUID","CAP_SYS_CHROOT"]' ]] \
  || fail "container effective capabilities differ from the reviewed set"
[[ "$("${PODMAN[@]}" exec "${CONTAINER}" \
  stat -c '%U:%G:%a' /srv/portfolio-sidecar)" == 'root:root:755' ]] \
  || fail "SFTP chroot permissions are unsafe"
[[ "$("${PODMAN[@]}" exec "${CONTAINER}" \
  stat -c '%U:%G:%a' /srv/portfolio-sidecar/repository)" \
  == 'sidecarbackup:sidecarbackup:700' ]] \
  || fail "Restic repository volume permissions are unsafe"
[[ -z "$("${PODMAN[@]}" exec "${CONTAINER}" \
  find /etc/ssh -maxdepth 1 -type f -name 'ssh_host_*' -print -quit)" ]] \
  || fail "the image retained package-generated SSH host keys"

"${PODMAN[@]}" rm --force "${CONTAINER}" >/dev/null
CREATED_CONTAINER=0
start_target '127.0.0.1::2222/tcp'
configure_client_endpoint
wait_for_health
printf '%s\n' \
  'get /repository/payload.txt persisted.txt' \
  >"${FIXTURE_ROOT}/sftp-persistence.batch"
(
  cd "${FIXTURE_ROOT}"
  sftp "${sftp_common[@]}" \
    -b "${FIXTURE_ROOT}/sftp-persistence.batch" \
    sidecarbackup@127.0.0.1
)
cmp "${FIXTURE_ROOT}/payload.txt" "${FIXTURE_ROOT}/persisted.txt" \
  || fail "the Restic repository volume did not survive container recreation"

printf 'Production SFTP target Podman smoke test passed.\n'
