#!/usr/bin/env bash
# Parse one inert target bundle with the real rootless Quadlet generator.

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MACHINE="${TRACTION_CONTROL_PODMAN_MACHINE:-${TRACTION_CONTROL_PODMAN_CONNECTION:-podman-machine-default}}"
RUN_TOKEN="$$"
FIXTURE_ROOT="$(mktemp -d "${REPO_ROOT}/config/portfolio-sidecar/quadlet-generator.local.XXXXXX")"
DEPLOYMENT="${FIXTURE_ROOT}/podman-mesh.local.json"
TARGETS="${FIXTURE_ROOT}/targets.local.json"
RENDERED="${FIXTURE_ROOT}/rendered.local.d"
GENERATOR_OUTPUT="${FIXTURE_ROOT}/quadlet-generator.out"
REMOTE_HOME=""
REMOTE_ROOT=""
REMOTE_CREATED=0
UNIT_NAME="portfolio-sidecar-target-001"
VOLUME_NAME="portfolio-sidecar-repository-001"
HOST_SECRET="portfolio-sidecar-host-key-001"
AUTHORIZED_SECRET="portfolio-sidecar-authorized-keys-001"
GENERATED_TARGET="${FIXTURE_ROOT}/${UNIT_NAME}.service"
GENERATED_VOLUME="${FIXTURE_ROOT}/${UNIT_NAME}-repository-volume.service"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

machine_ssh() {
  podman machine ssh "${MACHINE}" "$@"
}

assert_no_runtime_resources() {
  if podman --connection "${MACHINE}" container exists "${UNIT_NAME}"; then
    fail "the Quadlet dry run created its target container"
  fi
  if podman --connection "${MACHINE}" volume exists "${VOLUME_NAME}"; then
    fail "the Quadlet dry run created its repository volume"
  fi
  if podman --connection "${MACHINE}" secret exists "${HOST_SECRET}"; then
    fail "the Quadlet dry run created its host-key secret"
  fi
  if podman --connection "${MACHINE}" secret exists "${AUTHORIZED_SECRET}"; then
    fail "the Quadlet dry run created its authorized-keys secret"
  fi
}

cleanup() {
  local original_status="$?"

  if [[ "${REMOTE_CREATED}" -eq 1 ]]; then
    case "${REMOTE_ROOT}" in
      /var/home/*/.local/share/traction-control/quadlet-generator-test-*)
        machine_ssh rm -rf -- "${REMOTE_ROOT}" >/dev/null 2>&1 || true
        ;;
    esac
  fi
  case "${FIXTURE_ROOT}" in
    "${REPO_ROOT}"/config/portfolio-sidecar/quadlet-generator.local.*)
      rm -rf "${FIXTURE_ROOT}"
      ;;
  esac
  exit "${original_status}"
}
trap cleanup EXIT HUP INT TERM

for command_name in awk cp git podman python3; do
  command -v "${command_name}" >/dev/null 2>&1 \
    || fail "${command_name} is required"
done
[[ "$(uname -s)" == "Darwin" ]] \
  || fail "this integration test targets the macOS Podman Machine verifier"
[[ "${MACHINE}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] \
  || fail "the Podman machine name is unsafe"
podman --connection "${MACHINE}" info >/dev/null \
  || fail "the selected Podman connection is unavailable"
machine_ssh test -x /usr/libexec/podman/quadlet \
  || fail "the Podman VM has no Quadlet generator"
machine_ssh test -d "${REPO_ROOT}" \
  || fail "the repository is not shared into the Podman VM"

REMOTE_HOME="$(machine_ssh pwd)"
[[ "${REMOTE_HOME}" =~ ^/var/home/[A-Za-z0-9._-]+$ ]] \
  || fail "the Podman VM returned an unsafe home directory"
REMOTE_ROOT="${REMOTE_HOME}/.local/share/traction-control/quadlet-generator-test-${RUN_TOKEN}"
machine_ssh test ! -e "${REMOTE_ROOT}" \
  || fail "the temporary Quadlet VM directory already exists"

cp "${REPO_ROOT}/config/portfolio-sidecar/podman-mesh.example.json" \
  "${DEPLOYMENT}"
cp "${REPO_ROOT}/config/portfolio-sidecar/targets.example.json" "${TARGETS}"
chmod 0600 "${DEPLOYMENT}" "${TARGETS}"
python3 "${REPO_ROOT}/scripts/render_portfolio_sidecar_quadlets.py" render \
  --deployment "${DEPLOYMENT}" \
  --targets "${TARGETS}" \
  --target-id TARGET_EXAMPLE_MESH_001 \
  --output "${RENDERED}"

[[ -f "${RENDERED}/${UNIT_NAME}.container" ]] \
  || fail "the target container Quadlet was not rendered"
[[ -f "${RENDERED}/${UNIT_NAME}-repository.volume" ]] \
  || fail "the target volume Quadlet was not rendered"
[[ -f "${RENDERED}/manifest.json" ]] \
  || fail "the target manifest was not rendered"
assert_no_runtime_resources

machine_ssh mkdir -p "${REMOTE_ROOT}"
REMOTE_CREATED=1
machine_ssh cp \
  "${RENDERED}/${UNIT_NAME}.container" \
  "${REMOTE_ROOT}/${UNIT_NAME}.container"
machine_ssh cp \
  "${RENDERED}/${UNIT_NAME}-repository.volume" \
  "${REMOTE_ROOT}/${UNIT_NAME}-repository.volume"
machine_ssh chmod 0600 \
  "${REMOTE_ROOT}/${UNIT_NAME}.container" \
  "${REMOTE_ROOT}/${UNIT_NAME}-repository.volume"

if ! machine_ssh env "QUADLET_UNIT_DIRS=${REMOTE_ROOT}" \
  /usr/libexec/podman/quadlet -user -dryrun -v \
  >"${GENERATOR_OUTPUT}" 2>&1; then
  sed -n '1,240p' "${GENERATOR_OUTPUT}" >&2
  fail "the real Quadlet generator rejected the rendered target"
fi

extract_generated_service() {
  local service_name="$1"
  local output_path="$2"

  awk -v marker="---${service_name}---" '
    $0 == marker { capture = 1; next }
    capture && /^---.*---$/ { exit }
    capture { print }
  ' "${GENERATOR_OUTPUT}" >"${output_path}"
  [[ -s "${output_path}" ]] \
    || fail "the generator emitted no ${service_name} content"
}

extract_generated_service "${UNIT_NAME}.service" "${GENERATED_TARGET}"
extract_generated_service \
  "${UNIT_NAME}-repository-volume.service" \
  "${GENERATED_VOLUME}"

grep -Fq "Loading source unit file ${REMOTE_ROOT}/${UNIT_NAME}.container" \
  "${GENERATOR_OUTPUT}" \
  || fail "the generator did not parse the target container Quadlet"
grep -Fq "Loading source unit file ${REMOTE_ROOT}/${UNIT_NAME}-repository.volume" \
  "${GENERATOR_OUTPUT}" \
  || fail "the generator did not parse the target volume Quadlet"
grep -Fq -- '--publish 10.77.0.11:2222:2222/tcp' "${GENERATED_TARGET}" \
  || fail "the generated service lost the exact mesh bind"
grep -Fq -- '--cap-drop all' "${GENERATED_TARGET}" \
  || fail "the generated service lost its capability drop"
grep -Fq -- '--cap-add sys_chroot' "${GENERATED_TARGET}" \
  || fail "the generated service lost the reviewed chroot capability"
grep -Fq -- "--secret source=${HOST_SECRET},target=sidecar-host-key" \
  "${GENERATED_TARGET}" \
  || fail "the generated service lost the host-key secret reference"
grep -Fq "Requires=${UNIT_NAME}-repository-volume.service" \
  "${GENERATED_TARGET}" \
  || fail "the generated service lost its volume dependency"
if grep -Fq '[Install]' "${GENERATED_TARGET}" "${GENERATED_VOLUME}" \
  || grep -Fq 'WantedBy=' "${GENERATED_TARGET}" "${GENERATED_VOLUME}"; then
  fail "the generator made the review-only target installable"
fi
if grep -Fq 'portfolio-sidecar-coordinator' \
  "${GENERATED_TARGET}" "${GENERATED_VOLUME}"; then
  fail "the target-specific bundle exposed a coordinator artifact"
fi

machine_ssh systemd-analyze --user verify \
  "${GENERATED_VOLUME}" \
  "${GENERATED_TARGET}" \
  || fail "systemd-analyze rejected the generated user services"

assert_no_runtime_resources
machine_ssh test ! -e "${REMOTE_HOME}/.config/systemd/user/${UNIT_NAME}.service" \
  || fail "the dry run installed a generated service"

printf 'Render-only target passed the real rootless Quadlet generator dry run.\n'
