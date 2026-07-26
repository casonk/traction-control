#!/usr/bin/env bash
# Load an optional local environment file, apply launch jitter, and execute one
# traction-control workload. This is primarily the launchd runtime adapter.

set -euo pipefail

JOB_NAME=""
ENV_FILE=""
DELAY_SECONDS=0
JITTER_SECONDS=0

usage() {
  cat <<'EOF'
Usage: run_traction_control_job.sh --job NAME [options] -- COMMAND [ARG ...]

Options:
  --env-file PATH         Source PATH with automatic export when it exists.
  --delay-seconds N       Fixed delay before execution (default: 0).
  --jitter-seconds N      Add a random delay from 0 through N (default: 0).
  --help                  Show this help text.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

trim_whitespace() {
  printf '%s' "$1" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//'
}

load_private_env_file() {
  local env_path="$1"
  local file_owner=""
  local file_mode=""
  local file_mode_value=0
  local env_line=""
  local env_key=""
  local env_value=""
  local value_length=0
  local first_character=""
  local last_character=""

  [[ ! -L "${env_path}" ]] || fail "environment file cannot be a symlink: ${env_path}"
  if file_owner="$(stat -f '%u' "${env_path}" 2>/dev/null)" \
    && file_mode="$(stat -f '%Lp' "${env_path}" 2>/dev/null)"; then
    :
  else
    file_owner="$(stat -c '%u' "${env_path}" 2>/dev/null)" \
      || fail "cannot inspect environment file owner: ${env_path}"
    file_mode="$(stat -c '%a' "${env_path}" 2>/dev/null)" \
      || fail "cannot inspect environment file mode: ${env_path}"
  fi
  [[ "${file_owner}" == "$(id -u)" ]] \
    || fail "environment file must be owned by the current user: ${env_path}"
  [[ "${file_mode}" =~ ^[0-7]+$ ]] || fail "invalid environment file mode: ${env_path}"
  file_mode_value=$(( 8#${file_mode} ))
  (( (file_mode_value & 0022) == 0 )) \
    || fail "environment file must not be writable by group or others: ${env_path}"

  while IFS= read -r env_line || [[ -n "${env_line}" ]]; do
    env_line="${env_line%$'\r'}"
    env_line="$(trim_whitespace "${env_line}")"
    [[ -n "${env_line}" ]] || continue
    case "${env_line}" in \#*) continue ;; esac
    case "${env_line}" in
      export[[:space:]]*) env_line="$(trim_whitespace "${env_line#export}")" ;;
    esac
    [[ "${env_line}" == *=* ]] || fail "invalid environment entry in ${env_path}: ${env_line}"
    env_key="$(trim_whitespace "${env_line%%=*}")"
    env_value="$(trim_whitespace "${env_line#*=}")"
    [[ "${env_key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] \
      || fail "invalid environment key in ${env_path}: ${env_key}"
    case "${env_key}" in
      JOB_NAME|ENV_FILE|DELAY_SECONDS|JITTER_SECONDS|RUNNER_*)
        fail "reserved runner key in ${env_path}: ${env_key}"
        ;;
    esac

    value_length="${#env_value}"
    if (( value_length >= 2 )); then
      first_character="${env_value:0:1}"
      last_character="${env_value:$(( value_length - 1 )):1}"
      if [[ "${first_character}" == '"' && "${last_character}" == '"' ]] \
        || [[ "${first_character}" == "'" && "${last_character}" == "'" ]]; then
        env_value="${env_value:1:$(( value_length - 2 ))}"
      fi
    fi
    export "${env_key}=${env_value}"
  done < "${env_path}"
}

need_value() {
  [[ $# -ge 2 ]] || fail "$1 requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --job)
      need_value "$@"
      JOB_NAME="$2"
      shift 2
      ;;
    --env-file)
      need_value "$@"
      ENV_FILE="$2"
      shift 2
      ;;
    --delay-seconds)
      need_value "$@"
      DELAY_SECONDS="$2"
      shift 2
      ;;
    --jitter-seconds)
      need_value "$@"
      JITTER_SECONDS="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "${JOB_NAME}" ]] || fail "--job is required"
[[ $# -gt 0 ]] || fail "a command is required after --"
[[ "${DELAY_SECONDS}" =~ ^[0-9]+$ ]] || fail "--delay-seconds must be a non-negative integer"
[[ "${JITTER_SECONDS}" =~ ^[0-9]+$ ]] || fail "--jitter-seconds must be a non-negative integer"

RUNNER_JOB_NAME="${JOB_NAME}"
RUNNER_ENV_FILE="${ENV_FILE}"
RUNNER_DELAY_SECONDS="${DELAY_SECONDS}"
RUNNER_JITTER_SECONDS="${JITTER_SECONDS}"
RUNNER_COMMAND=("$@")
readonly RUNNER_JOB_NAME RUNNER_ENV_FILE RUNNER_DELAY_SECONDS RUNNER_JITTER_SECONDS
readonly -a RUNNER_COMMAND

if [[ -n "${RUNNER_ENV_FILE}" && -f "${RUNNER_ENV_FILE}" ]]; then
  load_private_env_file "${RUNNER_ENV_FILE}"
fi

sleep_seconds="${RUNNER_DELAY_SECONDS}"
if (( RUNNER_JITTER_SECONDS > 0 )); then
  sleep_seconds=$(( sleep_seconds + (RANDOM % (RUNNER_JITTER_SECONDS + 1)) ))
fi

if (( sleep_seconds > 0 )); then
  printf 'info: %s delaying execution for %s seconds\n' "${RUNNER_JOB_NAME}" "${sleep_seconds}"
  /bin/sleep "${sleep_seconds}"
fi

exec "${RUNNER_COMMAND[@]}"
