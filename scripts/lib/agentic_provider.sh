#!/usr/bin/env bash
# Shared provider selection and readiness-precheck helpers for unattended
# agentic maintenance jobs.

agentic_fail() {
  if declare -F fail >/dev/null 2>&1; then
    fail "$@"
  fi
  printf 'error: %s\n' "$*" >&2
  exit 1
}

agentic_log() {
  if declare -F log >/dev/null 2>&1; then
    log "$@"
  fi
}

agentic_trim_output() {
  printf '%s' "$1" | tr '\r\n\t' '   ' | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//'
}

agentic_output_indicates_capacity_issue() {
  printf '%s' "$1" | grep -Eiq \
    'overage|over[- ]?quota|quota exceeded|insufficient credits?|credit balance|usage limit|limit reached|rate limit|too many requests|model is overloaded|temporarily unavailable|payment required'
}

agentic_provider_status_command() {
  local provider="$1"

  case "${provider}" in
    codex)
      command -v codex >/dev/null 2>&1 || return 127
      codex login status
      ;;
    claude)
      command -v claude >/dev/null 2>&1 || return 127
      claude auth status
      ;;
    copilot)
      command -v copilot >/dev/null 2>&1 || return 127
      if command -v gh >/dev/null 2>&1; then
        gh auth status
      else
        copilot version
      fi
      ;;
    *)
      return 2
      ;;
  esac
}

agentic_provider_readiness_probe() {
  local provider="$1"
  local model="${2:-}"
  local prompt="Reply READY and nothing else."

  case "${provider}" in
    codex)
      local cmd=(codex exec --sandbox read-only --ephemeral --skip-git-repo-check --color never)
      if [[ -n "${REPO_ROOT:-}" ]]; then
        cmd+=(-C "${REPO_ROOT}")
      fi
      if [[ -n "${model}" ]]; then
        cmd+=(--model "${model}")
      fi
      printf '%s\n' "${prompt}" | "${cmd[@]}" -
      ;;
    claude)
      local cmd=(
        claude
        --print
        --output-format text
        --permission-mode dontAsk
        --no-session-persistence
        --tools ""
      )
      if [[ -n "${model}" ]]; then
        cmd+=(--model "${model}")
      fi
      "${cmd[@]}" "${prompt}"
      ;;
    copilot)
      local cmd=(
        copilot
        --prompt "${prompt}"
        --no-ask-user
        --silent
        --available-tools ""
      )
      if [[ -n "${model}" ]]; then
        cmd+=(--model "${model}")
      fi
      "${cmd[@]}"
      ;;
    *)
      return 2
      ;;
  esac
}

agentic_provider_ready() {
  local provider="$1"
  local model="${2:-}"
  local status_output=""
  local status_rc=0
  local probe_output=""
  local probe_rc=0
  local summary=""

  set +e
  status_output="$(agentic_provider_status_command "${provider}" 2>&1)"
  status_rc=$?
  set -e
  summary="$(agentic_trim_output "${status_output}")"

  if (( status_rc != 0 )); then
    agentic_log "provider check  : ${provider} status unavailable (${status_rc})${summary:+ — ${summary}}"
    return 1
  fi
  if agentic_output_indicates_capacity_issue "${status_output}"; then
    agentic_log "provider check  : ${provider} status reports capacity issue${summary:+ — ${summary}}"
    return 1
  fi
  agentic_log "provider check  : ${provider} status OK${summary:+ — ${summary}}"

  set +e
  probe_output="$(agentic_provider_readiness_probe "${provider}" "${model}" 2>&1)"
  probe_rc=$?
  set -e
  summary="$(agentic_trim_output "${probe_output}")"

  if (( probe_rc != 0 )); then
    agentic_log "provider check  : ${provider} readiness probe failed (${probe_rc})${summary:+ — ${summary}}"
    return 1
  fi
  if agentic_output_indicates_capacity_issue "${probe_output}"; then
    agentic_log "provider check  : ${provider} readiness probe reports capacity issue${summary:+ — ${summary}}"
    return 1
  fi

  agentic_log "provider check  : ${provider} ready${model:+ for model ${model}}"
  return 0
}

agentic_resolve_provider() {
  local requested="$1"
  local model="${2:-}"
  local candidate=""

  case "${requested}" in
    auto)
      for candidate in codex claude copilot; do
        if agentic_provider_ready "${candidate}" "${model}"; then
          printf '%s\n' "${candidate}"
          return 0
        fi
      done
      return 1
      ;;
    codex|claude|copilot)
      agentic_provider_ready "${requested}" "${model}" || return 1
      printf '%s\n' "${requested}"
      ;;
    *)
      agentic_fail "unsupported provider: ${requested}"
      ;;
  esac
}
