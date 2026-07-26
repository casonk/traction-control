#!/usr/bin/env bash
# Run the private-sidecar security regressions in an isolated Linux container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONTAINERFILE="${REPO_ROOT}/tests/containers/Sidecar.Containerfile"
IMAGE_TAG="${TRACTION_CONTROL_SIDECAR_IMAGE:-traction-control-sidecar-test:local}"
CONTEXT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/traction-control-sidecar-context.XXXXXX")"
ENGINE="${CONTAINER_ENGINE:-}"
PODMAN_CONNECTION="${TRACTION_CONTROL_PODMAN_CONNECTION:-}"

cleanup() {
  case "${CONTEXT_ROOT}" in
    "${TMPDIR:-/tmp}"/traction-control-sidecar-context.*)
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
  || fail "no container engine found; run scripts/install_podman_runtime.sh"
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
mkdir -p "${CONTEXT_ROOT}/scripts" "${CONTEXT_ROOT}/tests"

for source_name in \
  portfolio_materializer.py \
  portfolio_sidecar.py \
  repository_visibility.py; do
  [[ -f "${REPO_ROOT}/scripts/${source_name}" ]] \
    || fail "required script not found: ${source_name}"
  cp -p \
    "${REPO_ROOT}/scripts/${source_name}" \
    "${CONTEXT_ROOT}/scripts/${source_name}"
done

for test_name in \
  test_portfolio_sidecar.py \
  test_portfolio_sidecar_hardening.py; do
  [[ -f "${REPO_ROOT}/tests/${test_name}" ]] \
    || fail "required test not found: ${test_name}"
  cp -p \
    "${REPO_ROOT}/tests/${test_name}" \
    "${CONTEXT_ROOT}/tests/${test_name}"
done

printf 'Building %s with %s...\n' "${IMAGE_TAG}" "${ENGINE_COMMAND[*]}"
"${ENGINE_COMMAND[@]}" build \
  --file "${CONTAINERFILE}" \
  --tag "${IMAGE_TAG}" \
  "${CONTEXT_ROOT}"

printf '\nRunning sidecar security regressions in a fresh networkless container...\n'
"${ENGINE_COMMAND[@]}" run \
  --rm \
  --init \
  --network none \
  --read-only \
  --cap-drop all \
  --security-opt no-new-privileges \
  --tmpfs /tmp:rw,exec,nosuid,nodev,size=256m \
  --name "traction-control-sidecar-test-$$" \
  "${IMAGE_TAG}"

printf '\nPrivate-sidecar container regressions passed.\n'
