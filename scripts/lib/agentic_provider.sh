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

# Providers tried in this order by "auto". "local" is a model served by the
# local Ollama instance, driven through codex's open-source provider mode.
AGENTIC_PROVIDER_ORDER=(codex claude copilot local)

# The local provider is a last resort and is off unless asked for: a local
# model is weaker at unattended repository edits than the hosted ones, and
# running it heats the GPU for the length of the job.
AGENTIC_LOCAL_FALLBACK="${AGENTIC_LOCAL_FALLBACK:-0}"
AGENTIC_LOCAL_MODEL="${AGENTIC_LOCAL_MODEL:-qwen3:32b}"
AGENTIC_OLLAMA_URL="${AGENTIC_OLLAMA_URL:-http://127.0.0.1:11434}"

agentic_local_status() {
  if [[ "${AGENTIC_LOCAL_FALLBACK}" != "1" ]]; then
    printf 'local provider disabled (set AGENTIC_LOCAL_FALLBACK=1)\n'
    return 3
  fi
  command -v codex >/dev/null 2>&1 || return 127
  command -v curl >/dev/null 2>&1 || return 127
  local tags=""
  tags="$(curl -fsS --max-time 5 "${AGENTIC_OLLAMA_URL}/api/tags")" || {
    printf 'ollama not reachable at %s\n' "${AGENTIC_OLLAMA_URL}"
    return 4
  }
  if [[ "${tags}" != *"\"name\":\"${AGENTIC_LOCAL_MODEL}\""* ]]; then
    printf 'ollama has no model %s\n' "${AGENTIC_LOCAL_MODEL}"
    return 5
  fi
  printf 'ollama serving %s\n' "${AGENTIC_LOCAL_MODEL}"
}

# Run the job's own run_codex against the local model: a codex function
# shadows the binary for the call and adds the open-source provider flags, so
# each job's prompt and sandbox plumbing is reused unchanged.
agentic_run_local() {
  local MODEL_REQUESTED="${AGENTIC_LOCAL_MODEL}"
  local rc=0
  codex() {
    if [[ "${1:-}" == "exec" ]]; then
      shift
      command codex exec --oss --local-provider ollama "$@"
    else
      command codex "$@"
    fi
  }
  run_codex || rc=$?
  unset -f codex
  return "${rc}"
}

# A checkout counts as busy, and unattended agents leave it alone, when an
# agent process is working inside it or when any file Git would see (tracked,
# or untracked but not ignored) changed within AGENTIC_QUIET_SECONDS. Both
# checks are deterministic and run before any provider is contacted.
AGENTIC_QUIET_SECONDS="${AGENTIC_QUIET_SECONDS:-900}"
AGENTIC_SKIP_PROCESS_CHECK="${AGENTIC_SKIP_PROCESS_CHECK:-0}"

agentic_agent_process_in() {
  local dir="$1"
  local ancestors=" "
  local pid="$$"
  local proc=""
  local name=""
  local cwd=""

  while [[ -n "${pid}" && "${pid}" -gt 1 ]]; do
    ancestors+="${pid} "
    pid="$(awk '/^PPid:/ {print $2}' "/proc/${pid}/status" 2>/dev/null || true)"
  done

  for proc in /proc/[0-9]*; do
    pid="${proc#/proc/}"
    [[ "${ancestors}" == *" ${pid} "* ]] && continue
    name="$(cat "${proc}/comm" 2>/dev/null)" || continue
    case "${name}" in
      codex|claude|copilot) ;;
      node)
        tr '\0' ' ' < "${proc}/cmdline" 2>/dev/null \
          | grep -Eq '/(codex|claude|copilot)( |$)' || continue
        ;;
      *) continue ;;
    esac
    cwd="$(readlink "${proc}/cwd" 2>/dev/null)" || continue
    if [[ "${cwd}" == "${dir}" || "${cwd}" == "${dir}/"* ]]; then
      printf 'agent process %s (%s) is working in %s\n' "${pid}" "${name}" "${cwd}"
      return 0
    fi
  done
  return 1
}

agentic_newest_edit() {
  local dir="$1"
  local head_log=""
  {
    git -C "${dir}" ls-files -z --cached --others --exclude-standard 2>/dev/null \
      | (cd "${dir}" && xargs -0 -r stat -c '%Y %n' -- 2>/dev/null)
    # HEAD's reflog moves on commit, checkout and reset. The index is no
    # signal: a plain git status rewrites it.
    head_log="$(git -C "${dir}" rev-parse --path-format=absolute --git-path logs/HEAD 2>/dev/null || true)"
    if [[ -n "${head_log}" && -f "${head_log}" ]]; then
      stat -c '%Y (git HEAD reflog)' -- "${head_log}"
    fi
  } | sort -n | tail -n 1
}

# Print why DIR is busy and return 0; return 1 when it is quiet.
agentic_checkout_busy() {
  local dir=""
  local newest=""
  local age=0
  dir="$(cd "$1" 2>/dev/null && pwd -P)" || return 1

  if [[ "${AGENTIC_SKIP_PROCESS_CHECK}" != "1" ]] && agentic_agent_process_in "${dir}"; then
    return 0
  fi
  if (( AGENTIC_QUIET_SECONDS > 0 )); then
    newest="$(agentic_newest_edit "${dir}")"
    if [[ -n "${newest}" ]]; then
      age=$(( $(date +%s) - ${newest%% *} ))
      if (( age < AGENTIC_QUIET_SECONDS )); then
        printf '%s changed %ss ago (quiet window %ss)\n' "${newest#* }" "${age}" "${AGENTIC_QUIET_SECONDS}"
        return 0
      fi
    fi
  fi
  return 1
}

# Exit the job successfully, without contacting a provider, when the checkout
# the agent would work in is busy. FORCE=1 skips the check.
agentic_defer_if_busy() {
  local dir="$1"
  local reason=""
  (( ${FORCE:-0} == 0 )) || return 0
  reason="$(agentic_checkout_busy "${dir}")" || return 0
  agentic_log "deferred        : ${dir} is busy — ${reason}"
  exit 0
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
    local)
      agentic_local_status
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
    local)
      # A prompt round trip loads a ~20 GB model and took about four minutes
      # on the reference host, so the probe only confirms the model is served.
      agentic_local_status
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
      for candidate in "${AGENTIC_PROVIDER_ORDER[@]}"; do
        if agentic_provider_ready "${candidate}" "${model}" >&2; then
          printf '%s\n' "${candidate}"
          return 0
        fi
      done
      return 1
      ;;
    codex|claude|copilot|local)
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
  local order=("${AGENTIC_PROVIDER_ORDER[@]}")
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
    if [[ "${PROVIDER}" == "local" ]] && ! declare -F run_local >/dev/null; then
      agentic_run_local > "${attempt_file}" 2>&1 || rc=$?
    else
      "run_${PROVIDER}" > "${attempt_file}" 2>&1 || rc=$?
    fi
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
