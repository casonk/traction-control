#!/usr/bin/env bash
# template_consolidation_agentic.sh — unattended shared-template consolidation
# pass across repo SECURITY.md and LESSONSLEARNED.md files.
#
# Prefers Codex when available, with Claude and Copilot as fallback providers.
# Skips the run when tracked policy files are already dirty to avoid trampling
# in-progress manual edits.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
PROMPT_FILE_DEFAULT="${REPO_ROOT}/config/prompts/template-consolidation-agentic.md"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/template-consolidation-agentic}"
PROMPT_FILE="${PROMPT_FILE:-${PROMPT_FILE_DEFAULT}}"
PROVIDER_REQUESTED="${TEMPLATE_CONSOLIDATION_PROVIDER:-auto}"
MODEL_REQUESTED="${TEMPLATE_CONSOLIDATION_MODEL:-}"
MAX_DEPTH=4
FORCE=0

source "${REPO_ROOT}/scripts/lib/agentic_provider.sh"

usage() {
  cat <<EOF
Usage: template_consolidation_agentic.sh [options]

Run an unattended agentic pass that reviews repo SECURITY.md and
LESSONSLEARNED.md files for guidance worth promoting into the shared templates.

Options:
  --provider NAME        Provider to use: auto, codex, claude, or copilot.
  --model MODEL          Optional model override for the selected provider.
  --portfolio-root PATH  Override the portfolio scan root.
  --log-dir DIR          Override the run-log directory.
  --prompt-file PATH     Override the maintenance prompt file.
  --force                Ignore dirty tracked policy files and run anyway.
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
    --provider)
      PROVIDER_REQUESTED="$2"
      shift 2
      ;;
    --model)
      MODEL_REQUESTED="$2"
      shift 2
      ;;
    --portfolio-root)
      PORTFOLIO_ROOT="$2"
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
    --force)
      FORCE=1
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

command -v git >/dev/null 2>&1 || fail "git not found"
[[ -d "${PORTFOLIO_ROOT}" ]] || fail "portfolio root does not exist: ${PORTFOLIO_ROOT}"
[[ -f "${PROMPT_FILE}" ]] || fail "missing prompt file: ${PROMPT_FILE}"

mkdir -p "${LOG_DIR}"
LOCK_FILE="${LOG_DIR}/template-consolidation-agentic.lock"
exec 9>"${LOCK_FILE}"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || {
    printf 'info: another template_consolidation_agentic.sh run is already active\n'
    exit 0
  }
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${LOG_DIR}/${TIMESTAMP}"
LOG_FILE="${RUN_DIR}/run.log"
AGENT_OUTPUT_FILE="${RUN_DIR}/agent-output.txt"
LAST_MESSAGE_FILE="${RUN_DIR}/last-message.txt"
LATEST_LOG_LINK="${LOG_DIR}/latest.log"
LATEST_OUTPUT_LINK="${LOG_DIR}/latest-output.txt"

mkdir -p "${RUN_DIR}"

PROMPT_TEXT="$(cat "${PROMPT_FILE}")"

log "=== template consolidation agentic run ==="
log "repo root       : ${REPO_ROOT}"
log "portfolio root  : ${PORTFOLIO_ROOT}"
log "provider req    : ${PROVIDER_REQUESTED}"
log "model override  : ${MODEL_REQUESTED:-<default>}"
log "provider probe  : auth/status + model readiness"
log "prompt file     : ${PROMPT_FILE}"
log "run dir         : ${RUN_DIR}"
log ""

PROVIDER="$(agentic_resolve_provider "${PROVIDER_REQUESTED}" "${MODEL_REQUESTED}")" \
  || fail "no ready agent provider found (codex, claude, copilot)"
log "provider used   : ${PROVIDER}"

mapfile -t REPO_DIRS < <(
  find "${PORTFOLIO_ROOT}" \
    -maxdepth "${MAX_DEPTH}" \
    -type d \
    -name ".git" \
    ! -path "*/archive-repos/*" \
  | sed 's|/.git$||' \
  | sort
)

log "found ${#REPO_DIRS[@]} repositories"

DIRTY_REPOS=()
if (( FORCE == 0 )); then
  for repo in "${REPO_DIRS[@]}"; do
    status="$(
      git -C "${repo}" status --porcelain -- \
        SECURITY.md \
        LESSONSLEARNED.md \
        docs/templates/SECURITY.md \
        docs/templates/LESSONSLEARNED.md \
        2>/dev/null || true
    )"
    if [[ -n "${status}" ]]; then
      DIRTY_REPOS+=("${repo#${PORTFOLIO_ROOT}/}")
    fi
  done
fi

if (( ${#DIRTY_REPOS[@]} > 0 )); then
  log "skip: tracked policy/template files are already dirty"
  for repo in "${DIRTY_REPOS[@]}"; do
    log "dirty repo      : ${repo}"
  done
  log "tip             : rerun with --force only if you intentionally want the agent to work across those edits"
  ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"
  exit 0
fi

run_codex() {
  local cmd=(codex exec --full-auto --color never -C "${REPO_ROOT}" --add-dir "${PORTFOLIO_ROOT}" -o "${LAST_MESSAGE_FILE}")
  if [[ -n "${MODEL_REQUESTED}" ]]; then
    cmd+=(--model "${MODEL_REQUESTED}")
  fi
  printf '%s\n' "${PROMPT_TEXT}" | "${cmd[@]}" -
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
  "${cmd[@]}" "${PROMPT_TEXT}"
}

run_copilot() {
  local cmd=(
    copilot
    --prompt "${PROMPT_TEXT}"
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
OUTPUT=""
set +e
case "${PROVIDER}" in
  codex)
    OUTPUT="$(run_codex 2>&1)"
    AGENT_STATUS=$?
    ;;
  claude)
    OUTPUT="$(run_claude 2>&1)"
    AGENT_STATUS=$?
    ;;
  copilot)
    OUTPUT="$(run_copilot 2>&1)"
    AGENT_STATUS=$?
    ;;
esac
set -e

printf '%s\n' "${OUTPUT}" | tee -a "${LOG_FILE}" > "${AGENT_OUTPUT_FILE}"
if [[ ! -s "${LAST_MESSAGE_FILE}" ]]; then
  cp "${AGENT_OUTPUT_FILE}" "${LAST_MESSAGE_FILE}"
fi

ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"
ln -sf "${AGENT_OUTPUT_FILE}" "${LATEST_OUTPUT_LINK}"

if (( AGENT_STATUS != 0 )); then
  log ""
  log "result          : FAILED (provider exit ${AGENT_STATUS})"
  exit "${AGENT_STATUS}"
fi

log ""
log "result          : OK"
exit 0
