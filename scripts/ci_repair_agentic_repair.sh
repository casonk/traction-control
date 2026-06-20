#!/usr/bin/env bash
# ci_repair_agentic_repair.sh — consume discovery output and run the unattended
# repair agent against candidate repos only.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
PROMPT_FILE_DEFAULT="${REPO_ROOT}/config/prompts/ci-repair-agentic.md"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
PROMPT_FILE="${PROMPT_FILE:-${PROMPT_FILE_DEFAULT}}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/ci-repair-agentic}"
PROVIDER_REQUESTED="${CI_REPAIR_AGENTIC_PROVIDER:-auto}"
MODEL_REQUESTED="${CI_REPAIR_AGENTIC_MODEL:-}"
CANDIDATE_FILE=""
INVENTORY_FILE=""

source "${REPO_ROOT}/scripts/lib/agentic_provider.sh"

usage() {
  cat <<EOF
Usage: ci_repair_agentic_repair.sh --candidate-file PATH --inventory-file PATH [options]

Run the unattended repair agent for candidate repos discovered by
ci_repair_agentic.sh --discovery-only.

Options:
  --candidate-file PATH  TSV file containing only candidate repo rows.
  --inventory-file PATH  Full discovery TSV inventory.
  --provider NAME        Provider to use: auto, codex, claude, or copilot.
  --model MODEL          Optional model override for the selected provider.
  --log-dir DIR          Override the run-log directory.
  --prompt-file PATH     Override the maintenance prompt file.
  --help                 Show this help text.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "${LOG_FILE}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-file)
      CANDIDATE_FILE="$2"
      shift 2
      ;;
    --inventory-file)
      INVENTORY_FILE="$2"
      shift 2
      ;;
    --provider)
      PROVIDER_REQUESTED="$2"
      shift 2
      ;;
    --model)
      MODEL_REQUESTED="$2"
      shift 2
      ;;
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --prompt-file)
      PROMPT_FILE="$2"
      shift 2
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

[[ -f "${PROMPT_FILE}" ]] || fail "missing prompt file: ${PROMPT_FILE}"
[[ -f "${CANDIDATE_FILE}" ]] || fail "missing candidate file: ${CANDIDATE_FILE}"
[[ -f "${INVENTORY_FILE}" ]] || fail "missing inventory file: ${INVENTORY_FILE}"

candidate_count="$(awk -F '\t' 'NR > 1 && $1 == "candidate" {count++} END {print count + 0}' "${CANDIDATE_FILE}")"
if (( candidate_count == 0 )); then
  printf 'info: no candidate repos found in %s\n' "${CANDIDATE_FILE}"
  exit 0
fi

mkdir -p "${LOG_DIR}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${LOG_DIR}/repair-${TIMESTAMP}"
LOG_FILE="${RUN_DIR}/run.log"
PROMPT_RUNTIME_FILE="${RUN_DIR}/prompt.txt"
AGENT_OUTPUT_FILE="${RUN_DIR}/agent-output.txt"
LAST_MESSAGE_FILE="${RUN_DIR}/last-message.txt"
LATEST_LOG_LINK="${LOG_DIR}/latest.log"
LATEST_OUTPUT_LINK="${LOG_DIR}/latest-output.txt"
mkdir -p "${RUN_DIR}"

log "=== CI repair execution ==="
log "repo root       : ${REPO_ROOT}"
log "portfolio root  : ${PORTFOLIO_ROOT}"
log "candidate file  : ${CANDIDATE_FILE}"
log "inventory file  : ${INVENTORY_FILE}"
log "provider req    : ${PROVIDER_REQUESTED}"
log "model override  : ${MODEL_REQUESTED:-<default>}"
log "run dir         : ${RUN_DIR}"
log ""

PROVIDER="$(agentic_resolve_provider "${PROVIDER_REQUESTED}" "${MODEL_REQUESTED}")" \
  || fail "no ready agent provider found (codex, claude, copilot)"
log "provider used   : ${PROVIDER}"

PROMPT_TEXT="$(cat "${PROMPT_FILE}")"
{
  printf '%s\n\n' "${PROMPT_TEXT}"
  printf 'Current candidate inventory (%s):\n' "${CANDIDATE_FILE}"
  printf '```tsv\n'
  cat "${CANDIDATE_FILE}"
  printf '```\n\n'
  printf 'Full discovery inventory (%s):\n' "${INVENTORY_FILE}"
  printf '```tsv\n'
  cat "${INVENTORY_FILE}"
  printf '```\n'
} > "${PROMPT_RUNTIME_FILE}"

run_codex() {
  local cmd=(codex exec --full-auto --color never -C "${REPO_ROOT}" --add-dir "${PORTFOLIO_ROOT}" -o "${LAST_MESSAGE_FILE}")
  if [[ -n "${MODEL_REQUESTED}" ]]; then
    cmd+=(--model "${MODEL_REQUESTED}")
  fi
  printf '%s\n' "$(cat "${PROMPT_RUNTIME_FILE}")" | "${cmd[@]}" -
}

run_claude() {
  local cmd=(
    claude
    --print
    --output-format text
    --permission-mode bypassPermissions
    --dangerously-skip-permissions
    --add-dir "${PORTFOLIO_ROOT}"
    --no-session-persistence
  )
  if [[ -n "${MODEL_REQUESTED}" ]]; then
    cmd+=(--model "${MODEL_REQUESTED}")
  fi
  "${cmd[@]}" "$(cat "${PROMPT_RUNTIME_FILE}")"
}

run_copilot() {
  local cmd=(
    copilot
    --prompt "$(cat "${PROMPT_RUNTIME_FILE}")"
    --yolo
    --no-ask-user
    --add-dir "${PORTFOLIO_ROOT}"
    --silent
  )
  if [[ -n "${MODEL_REQUESTED}" ]]; then
    cmd+=(--model "${MODEL_REQUESTED}")
  fi
  "${cmd[@]}"
}

AGENT_STATUS=0
set +e
case "${PROVIDER}" in
  codex)
    run_codex > "${AGENT_OUTPUT_FILE}" 2>&1
    AGENT_STATUS=$?
    ;;
  claude)
    run_claude > "${AGENT_OUTPUT_FILE}" 2>&1
    AGENT_STATUS=$?
    ;;
  copilot)
    run_copilot > "${AGENT_OUTPUT_FILE}" 2>&1
    AGENT_STATUS=$?
    ;;
esac
set -e

cat "${AGENT_OUTPUT_FILE}" >> "${LOG_FILE}"
ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"
ln -sf "${AGENT_OUTPUT_FILE}" "${LATEST_OUTPUT_LINK}"

if (( AGENT_STATUS != 0 )); then
  log "agent exit status: ${AGENT_STATUS}"
  exit "${AGENT_STATUS}"
fi

log "agent exit status: 0"
