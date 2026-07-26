#!/usr/bin/env bash
# Install Podman and prepare the rootless runtime used by the containerized
# traction-control installer tests.

set -euo pipefail

MACHINE_NAME="podman-machine-default"
MACHINE_CPUS="2"
MACHINE_MEMORY_MIB="4096"
MACHINE_DISK_GIB="30"
INSTALL_MISSING=1
SMOKE_TEST=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: install_podman_runtime.sh [options]

Install Podman when necessary and verify a usable container runtime. On macOS,
the script uses Homebrew for unattended installation, creates a rootless Podman
machine when missing, and starts it only when it is stopped. On Linux, it uses
the detected distribution package manager and runs Podman natively.

Options:
  --machine-name NAME     macOS machine name, 1-30 safe characters
                          (default: podman-machine-default).
  --cpus NUMBER           CPUs for a newly created machine (default: 2).
  --memory-mib NUMBER     Memory for a newly created machine (default: 4096).
  --disk-gib NUMBER       Disk size for a newly created machine (default: 30).
  --no-install            Require Podman to be installed already.
  --smoke-test            Pull and run quay.io/podman/hello:latest after setup.
  --dry-run               Print commands without writes, downloads, or VM changes.
  --help                  Show this help text.

Existing machines are never resized, reset, removed, or switched between
rootless and rootful mode. Podman Desktop and its privileged macOS helper are
not installed. Podman machine's default macOS configuration exposes the login
user's home directory to the VM; this script does not add any other mounts.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

info() {
  printf 'info: %s\n' "$*"
}

plan_command() {
  printf 'plan:'
  printf ' %q' "$@"
  printf '\n'
}

run_command() {
  if (( DRY_RUN == 1 )); then
    plan_command "$@"
  else
    "$@"
  fi
}

run_as_root() {
  if [[ "$(id -u)" == "0" ]]; then
    run_command "$@"
    return
  fi
  command -v sudo >/dev/null 2>&1 \
    || fail "sudo is required to install the Linux package"
  run_command sudo "$@"
}

validate_positive_integer() {
  local option_name="$1"
  local option_value="$2"
  [[ "${option_value}" =~ ^[1-9][0-9]*$ ]] \
    || fail "${option_name} must be a positive integer: ${option_value}"
}

install_linux_package() {
  if command -v apt-get >/dev/null 2>&1; then
    run_as_root apt-get update
    run_as_root apt-get install -y podman
  elif command -v dnf >/dev/null 2>&1; then
    run_as_root dnf install -y podman
  elif command -v zypper >/dev/null 2>&1; then
    run_as_root zypper --non-interactive install podman
  elif command -v apk >/dev/null 2>&1; then
    run_as_root apk add podman
  elif command -v pacman >/dev/null 2>&1; then
    run_as_root pacman -S --needed --noconfirm podman
  else
    fail "no supported Linux package manager found; install Podman manually"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --machine-name)
      [[ $# -ge 2 ]] || fail "--machine-name requires a value"
      MACHINE_NAME="$2"
      shift 2
      ;;
    --cpus)
      [[ $# -ge 2 ]] || fail "--cpus requires a value"
      MACHINE_CPUS="$2"
      shift 2
      ;;
    --memory-mib)
      [[ $# -ge 2 ]] || fail "--memory-mib requires a value"
      MACHINE_MEMORY_MIB="$2"
      shift 2
      ;;
    --disk-gib)
      [[ $# -ge 2 ]] || fail "--disk-gib requires a value"
      MACHINE_DISK_GIB="$2"
      shift 2
      ;;
    --no-install)
      INSTALL_MISSING=0
      shift
      ;;
    --smoke-test)
      SMOKE_TEST=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ "${MACHINE_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || fail "unsafe Podman machine name: ${MACHINE_NAME}"
(( ${#MACHINE_NAME} <= 30 )) \
  || fail "Podman machine name must be 30 characters or fewer"
validate_positive_integer "--cpus" "${MACHINE_CPUS}"
validate_positive_integer "--memory-mib" "${MACHINE_MEMORY_MIB}"
validate_positive_integer "--disk-gib" "${MACHINE_DISK_GIB}"

HOST_OS="$(uname -s)"
case "${HOST_OS}" in
  Darwin|Linux) ;;
  *) fail "unsupported host platform: ${HOST_OS}" ;;
esac

if [[ "${HOST_OS}" == "Darwin" && "$(id -u)" == "0" ]]; then
  fail "run this script as the macOS login user, not root"
fi

if command -v podman >/dev/null 2>&1; then
  info "Podman CLI is already installed: $(command -v podman)"
elif (( INSTALL_MISSING == 0 )); then
  fail "Podman is not installed and --no-install was passed"
elif [[ "${HOST_OS}" == "Darwin" ]]; then
  command -v brew >/dev/null 2>&1 \
    || fail "Homebrew not found; install the official Podman package, then rerun with --no-install"
  command -v sw_vers >/dev/null 2>&1 \
    || fail "cannot determine the macOS version for Homebrew compatibility"
  HOST_ARCH="$(uname -m)" || fail "cannot determine the macOS architecture"
  MACOS_VERSION="$(sw_vers -productVersion)" \
    || fail "cannot determine the macOS version"
  MACOS_MAJOR="${MACOS_VERSION%%.*}"
  [[ "${MACOS_MAJOR}" =~ ^[0-9]+$ ]] \
    || fail "unexpected macOS version: ${MACOS_VERSION}"
  if [[ "${HOST_ARCH}" != "arm64" || "${MACOS_MAJOR}" -lt 13 ]]; then
    fail "Homebrew Podman requires Apple Silicon and macOS 13+; install Podman's signed package, then rerun with --no-install"
  fi
  warn "automated macOS setup uses Homebrew; Podman upstream recommends its signed installer for manual installs"
  run_command brew install podman
else
  install_linux_package
fi

if (( DRY_RUN == 0 )); then
  hash -r
fi

if (( DRY_RUN == 1 )); then
  if [[ "${HOST_OS}" == "Darwin" ]]; then
    plan_command podman machine list --format '{{.Name}}'
    plan_command podman machine init \
      --rootful=false \
      --update-connection=false \
      --cpus "${MACHINE_CPUS}" \
      --memory "${MACHINE_MEMORY_MIB}" \
      --disk-size "${MACHINE_DISK_GIB}" \
      "${MACHINE_NAME}"
    plan_command podman machine start --update-connection=false "${MACHINE_NAME}"
    plan_command podman --connection "${MACHINE_NAME}" info
  else
    plan_command podman info
  fi
  if (( SMOKE_TEST == 1 )); then
    if [[ "${HOST_OS}" == "Darwin" ]]; then
      plan_command podman --connection "${MACHINE_NAME}" \
        run --rm --pull=missing quay.io/podman/hello:latest
    else
      plan_command podman run --rm --pull=missing quay.io/podman/hello:latest
    fi
  fi
  info "dry-run complete; no packages, machines, images, or containers were changed"
  exit 0
fi

command -v podman >/dev/null 2>&1 \
  || fail "Podman installation completed but the CLI is not on PATH"

if [[ "${HOST_OS}" == "Darwin" ]]; then
  MACHINE_STATE=""
  MACHINE_ROOTFUL=""
  MACHINE_EXISTS=0
  MACHINE_NAMES="$(podman machine list --format '{{.Name}}')" \
    || fail "cannot list Podman machines"
  while IFS= read -r listed_machine_name; do
    if [[ "${listed_machine_name}" == "${MACHINE_NAME}" ]]; then
      MACHINE_EXISTS=1
      break
    fi
  done <<EOF
${MACHINE_NAMES}
EOF

  if (( MACHINE_EXISTS == 1 )); then
    MACHINE_STATE="$(
      podman machine inspect --format '{{.State}}' "${MACHINE_NAME}"
    )" || fail "cannot inspect state for Podman machine ${MACHINE_NAME}"
    MACHINE_ROOTFUL="$(
      podman machine inspect --format '{{.Rootful}}' "${MACHINE_NAME}"
    )" || fail "cannot inspect rootful mode for Podman machine ${MACHINE_NAME}"
    [[ "${MACHINE_ROOTFUL}" == "false" ]] \
      || fail "existing machine ${MACHINE_NAME} is rootful; refusing to change or use it"
    info "Podman machine exists: ${MACHINE_NAME} (${MACHINE_STATE})"
  else
    run_command podman machine init \
      --rootful=false \
      --update-connection=false \
      --cpus "${MACHINE_CPUS}" \
      --memory "${MACHINE_MEMORY_MIB}" \
      --disk-size "${MACHINE_DISK_GIB}" \
      "${MACHINE_NAME}"
    MACHINE_STATE="$(
      podman machine inspect --format '{{.State}}' "${MACHINE_NAME}"
    )" || fail "cannot inspect new Podman machine ${MACHINE_NAME}"
    MACHINE_ROOTFUL="$(
      podman machine inspect --format '{{.Rootful}}' "${MACHINE_NAME}"
    )" || fail "cannot inspect new Podman machine mode ${MACHINE_NAME}"
    [[ "${MACHINE_ROOTFUL}" == "false" ]] \
      || fail "new machine ${MACHINE_NAME} is unexpectedly rootful"
  fi

  case "${MACHINE_STATE}" in
    running)
      info "Podman machine is already running: ${MACHINE_NAME}"
      ;;
    stopped)
      run_command podman machine start --update-connection=false "${MACHINE_NAME}"
      ;;
    *)
      fail "Podman machine ${MACHINE_NAME} is in unsupported state: ${MACHINE_STATE:-<empty>}"
      ;;
  esac

  MACHINE_STATE="$(
    podman machine inspect --format '{{.State}}' "${MACHINE_NAME}"
  )" || fail "cannot confirm state for Podman machine ${MACHINE_NAME}"
  [[ "${MACHINE_STATE}" == "running" ]] \
    || fail "Podman machine did not reach running state: ${MACHINE_STATE:-<empty>}"
fi

if [[ "${HOST_OS}" == "Darwin" ]]; then
  run_command podman --connection "${MACHINE_NAME}" info
else
  run_command podman info
fi

if (( SMOKE_TEST == 1 )); then
  if [[ "${HOST_OS}" == "Darwin" ]]; then
    run_command podman --connection "${MACHINE_NAME}" \
      run --rm --pull=missing quay.io/podman/hello:latest
  else
    run_command podman run --rm --pull=missing quay.io/podman/hello:latest
  fi
else
  info "container smoke test not requested; pass --smoke-test to pull and run it"
fi

if [[ "${HOST_OS}" == "Darwin" ]]; then
  info "Podman runtime is ready with rootless machine ${MACHINE_NAME}"
  info "container runner connection: TRACTION_CONTROL_PODMAN_CONNECTION=${MACHINE_NAME}"
else
  info "native Podman runtime is ready"
fi
