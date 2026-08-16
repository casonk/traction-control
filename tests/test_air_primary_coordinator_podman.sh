#!/usr/bin/env bash
# Exercise the Air-primary coordinator against the real sibling renderers in
# one isolated, networkless Podman container.

set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -P "${SCRIPT_DIR}/.." && pwd)"
UTIL_REPOS_ROOT="${TRACTION_CONTROL_UTIL_REPOS_ROOT:-$(cd -P "${REPO_ROOT}/.." && pwd)}"
CLOCKWORK_REPO="${TRACTION_CONTROL_CLOCKWORK_REPO:-${UTIL_REPOS_ROOT}/clockwork}"
SNOWBRIDGE_REPO="${TRACTION_CONTROL_SNOWBRIDGE_REPO:-${UTIL_REPOS_ROOT}/snowbridge}"
WIRING_HARNESS_REPO="${TRACTION_CONTROL_WIRING_HARNESS_REPO:-${UTIL_REPOS_ROOT}/wiring-harness}"
CONTAINER_SUPPORT="${REPO_ROOT}/tests/containers/air-primary"
CONTAINERFILE="${CONTAINER_SUPPORT}/Containerfile"
IMAGE_TAG="${TRACTION_CONTROL_AIR_PRIMARY_IMAGE:-localhost/traction-control-air-primary-test:local}"
PODMAN_CONNECTION="${TRACTION_CONTROL_PODMAN_CONNECTION:-}"
CONTEXT_PARENT="$(cd -P "${TMPDIR:-/tmp}" && pwd)"
CONTEXT_ROOT="$(mktemp -d "${CONTEXT_PARENT}/traction-control-air-primary-context.XXXXXX")"
CONTAINER_NAME="traction-control-air-primary-test-$$"

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

cleanup() {
  "${PODMAN[@]}" rm --force "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  case "${CONTEXT_ROOT}" in
    "${CONTEXT_PARENT}"/traction-control-air-primary-context.*)
      rm -rf "${CONTEXT_ROOT}"
      ;;
  esac
}
trap cleanup EXIT HUP INT TERM

command -v podman >/dev/null 2>&1 \
  || fail "Podman is required; run scripts/install_podman_runtime.sh"
"${PODMAN[@]}" version >/dev/null 2>&1 \
  || fail "Podman is installed but the selected connection is unavailable"

required_files=(
  "${CONTAINERFILE}"
  "${CONTAINER_SUPPORT}/run-test.sh"
  "${CONTAINER_SUPPORT}/assert_bundle.py"
  "${REPO_ROOT}/.gitignore"
  "${REPO_ROOT}/config/air-primary.example.toml"
  "${REPO_ROOT}/scripts/render_air_primary.py"
  "${CLOCKWORK_REPO}/scripts/run_clockwork_web_macos.sh"
  "${SNOWBRIDGE_REPO}/.gitignore"
  "${SNOWBRIDGE_REPO}/scripts/macos_smb_plan.py"
  "${WIRING_HARNESS_REPO}/scripts/render_macos_private_edge.py"
  "${WIRING_HARNESS_REPO}/scripts/site_registry.py"
)
for source_path in "${required_files[@]}"; do
  [[ -f "${source_path}" ]] || fail "required test source is missing: ${source_path}"
done

clockwork_modules=(
  __init__.py
  __main__.py
  cli.py
  manifest.py
  model.py
  render.py
)
for module_name in "${clockwork_modules[@]}"; do
  source_path="${CLOCKWORK_REPO}/src/clockwork/${module_name}"
  [[ -f "${source_path}" ]] || fail "required Clockwork module is missing: ${source_path}"
done

# Build an explicit allowlisted context. Never expose sibling .git directories,
# ignored local configuration, or the util-repos parent to the image builder.
mkdir -p \
  "${CONTEXT_ROOT}/repo-sources/traction-control/config" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/scripts" \
  "${CONTEXT_ROOT}/repo-sources/clockwork/scripts" \
  "${CONTEXT_ROOT}/repo-sources/clockwork/src/clockwork" \
  "${CONTEXT_ROOT}/repo-sources/snowbridge/scripts" \
  "${CONTEXT_ROOT}/repo-sources/wiring-harness/scripts" \
  "${CONTEXT_ROOT}/test-support"

cp -p "${REPO_ROOT}/.gitignore" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/.gitignore"
cp -p "${REPO_ROOT}/config/air-primary.example.toml" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/config/air-primary.example.toml"
cp -p "${REPO_ROOT}/scripts/render_air_primary.py" \
  "${CONTEXT_ROOT}/repo-sources/traction-control/scripts/render_air_primary.py"

for module_name in "${clockwork_modules[@]}"; do
  cp -p \
    "${CLOCKWORK_REPO}/src/clockwork/${module_name}" \
    "${CONTEXT_ROOT}/repo-sources/clockwork/src/clockwork/${module_name}"
done
cp -p \
  "${CLOCKWORK_REPO}/scripts/run_clockwork_web_macos.sh" \
  "${CONTEXT_ROOT}/repo-sources/clockwork/scripts/run_clockwork_web_macos.sh"

cp -p "${SNOWBRIDGE_REPO}/.gitignore" \
  "${CONTEXT_ROOT}/repo-sources/snowbridge/.gitignore"
cp -p "${SNOWBRIDGE_REPO}/scripts/macos_smb_plan.py" \
  "${CONTEXT_ROOT}/repo-sources/snowbridge/scripts/macos_smb_plan.py"

cp -p \
  "${WIRING_HARNESS_REPO}/scripts/render_macos_private_edge.py" \
  "${CONTEXT_ROOT}/repo-sources/wiring-harness/scripts/render_macos_private_edge.py"
cp -p \
  "${WIRING_HARNESS_REPO}/scripts/site_registry.py" \
  "${CONTEXT_ROOT}/repo-sources/wiring-harness/scripts/site_registry.py"

cp -p \
  "${CONTAINER_SUPPORT}/run-test.sh" \
  "${CONTAINER_SUPPORT}/assert_bundle.py" \
  "${CONTEXT_ROOT}/test-support/"

printf 'Building %s with %s...\n' "${IMAGE_TAG}" "${PODMAN[*]}"
"${PODMAN[@]}" build \
  --file "${CONTAINERFILE}" \
  --tag "${IMAGE_TAG}" \
  "${CONTEXT_ROOT}"

printf 'Running real Air-primary render regression in a networkless container...\n'
"${PODMAN[@]}" run \
  --rm \
  --name "${CONTAINER_NAME}" \
  --network none \
  --read-only \
  --cap-drop all \
  --cap-add NET_ADMIN \
  --security-opt no-new-privileges \
  --pids-limit 128 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,mode=1777,size=64m \
  --tmpfs /work:rw,noexec,nosuid,nodev,mode=0700,size=128m \
  "${IMAGE_TAG}"

printf 'Real Air-primary Podman regression passed.\n'
