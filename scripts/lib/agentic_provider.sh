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
  printf '%s' "$1" | tr '\r\n\t' '   ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//'
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
        if agentic_provider_ready "${candidate}" "${model}" >&2; then
          printf '%s\n' "${candidate}"
          return 0
        fi
      done
      return 1
      ;;
    codex|claude|copilot)
      agentic_provider_ready "${requested}" "${model}" >&2 || return 1
      printf '%s\n' "${requested}"
      ;;
    *)
      agentic_fail "unsupported provider: ${requested}"
      ;;
  esac
}

# A run that fails this soon after starting is treated as never having started
# (a removed CLI flag, an expired login, a full quota), so the next ready
# provider may take over without finding half-finished work.
AGENTIC_FALLBACK_WINDOW_SECONDS="${AGENTIC_FALLBACK_WINDOW_SECONDS:-120}"

# Run the job's run_<provider> function for PROVIDER, writing to OUTPUT_FILE.
# When the job asked for "auto" and the run fails early or reports a capacity
# problem, try each later provider that passes the readiness check. A provider
# named explicitly is never replaced. Sets PROVIDER to the provider that ran
# last and returns its exit status.
agentic_run_with_fallback() {
  local output_file="$1"
  local requested="$2"
  local model="${3:-}"
  local order=(codex claude copilot)
  local attempt_file="${output_file}.attempt"
  local started=0
  local elapsed=0
  local rc=0
  local passed_current=0
  local candidate=""

  : > "${output_file}"
  while :; do
    started="$(date +%s)"
    rc=0
    "run_${PROVIDER}" > "${attempt_file}" 2>&1 || rc=$?
    elapsed=$(( $(date +%s) - started ))
    cat "${attempt_file}" >> "${output_file}"

    (( rc != 0 )) || break
    [[ "${requested}" == "auto" ]] || break
    if (( elapsed >= AGENTIC_FALLBACK_WINDOW_SECONDS )) \
      && ! agentic_output_indicates_capacity_issue "$(cat "${attempt_file}")"; then
      agentic_log "provider run    : ${PROVIDER} failed (${rc}) after ${elapsed}s; not falling back"
      break
    fi

    agentic_log "provider run    : ${PROVIDER} failed (${rc}) after ${elapsed}s; trying the next provider"
    passed_current=0
    local next=""
    for candidate in "${order[@]}"; do
      if (( passed_current == 0 )); then
        [[ "${candidate}" == "${PROVIDER}" ]] && passed_current=1
        continue
      fi
      if agentic_provider_ready "${candidate}" "${model}" >&2; then
        next="${candidate}"
        break
      fi
    done
    [[ -n "${next}" ]] || break
    printf '\n--- %s failed (%s); falling back to %s ---\n\n' "${PROVIDER}" "${rc}" "${next}" >> "${output_file}"
    PROVIDER="${next}"
    agentic_log "provider used   : ${PROVIDER}"
  done

  rm -f "${attempt_file}"
  return "${rc}"
}
