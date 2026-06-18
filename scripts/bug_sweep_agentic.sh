#!/usr/bin/env bash
# bug_sweep_agentic.sh — unattended daily bug-review pass across clean
# code-focused repos in the local portfolio.
#
# The wrapper inventories clean repos with tracked source files, skips dirty
# worktrees by default, and invokes an agent CLI to perform a findings-first
# review. It is review-first by default; target-repo edits are exceptional.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
PROMPT_FILE_DEFAULT="${REPO_ROOT}/config/prompts/bug-sweep-agentic.md"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/bug-sweep-agentic}"
PROMPT_FILE="${PROMPT_FILE:-${PROMPT_FILE_DEFAULT}}"
PROVIDER_REQUESTED="${BUG_SWEEP_AGENTIC_PROVIDER:-auto}"
MODEL_REQUESTED="${BUG_SWEEP_AGENTIC_MODEL:-}"
MAX_DEPTH=4
FORCE=0
CODE_PATTERNS=(
  'pyproject.toml'
  'setup.py'
  '*.py'
  '*.sh'
  '*.bash'
  '*.zsh'
  '*.rb'
  '*.js'
  '*.jsx'
  '*.ts'
  '*.tsx'
  '*.go'
  '*.rs'
  '*.java'
  '*.c'
  '*.cc'
  '*.cpp'
  '*.h'
  '*.hpp'
  '*.cs'
  '*.php'
  'Gemfile'
  'Rakefile'
  'Makefile'
  'Dockerfile'
)

source "${REPO_ROOT}/scripts/lib/agentic_provider.sh"

usage() {
  cat <<EOF
Usage: bug_sweep_agentic.sh [options]

Inventory clean code repos in the portfolio and invoke an unattended agentic
review pass that looks for potential bugs and regressions.

Options:
  --provider NAME        Provider to use: auto, codex, claude, or copilot.
  --model MODEL          Optional model override for the selected provider.
  --portfolio-root PATH  Override the portfolio scan root.
  --log-dir DIR          Override the run-log directory.
  --prompt-file PATH     Override the maintenance prompt file.
  --force                Ignore dirty worktrees and review them anyway.
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

sanitize_field() {
  printf '%s' "$1" | tr '\r\n\t' '   ' | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//'
}

join_by() {
  local sep="$1"
  shift
  local first=1
  local item

  for item in "$@"; do
    if (( first == 0 )); then
      printf '%s' "${sep}"
    fi
    printf '%s' "${item}"
    first=0
  done
}

infer_code_kind() {
  local has_python=0
  local has_shell=0
  local has_ruby=0
  local has_other=0
  local path

  for path in "$@"; do
    case "${path}" in
      *.py|pyproject.toml|setup.py)
        has_python=1
        ;;
      *.sh|*.bash|*.zsh)
        has_shell=1
        ;;
      *.rb|Gemfile|Rakefile)
        has_ruby=1
        ;;
      *)
        has_other=1
        ;;
    esac
  done

  case "$(( has_python + has_shell + has_ruby + has_other ))" in
    0)
      printf 'none\n'
      ;;
    1)
      if (( has_python == 1 )); then
        printf 'python\n'
      elif (( has_shell == 1 )); then
        printf 'shell\n'
      elif (( has_ruby == 1 )); then
        printf 'ruby\n'
      else
        printf 'other\n'
      fi
      ;;
    *)
      printf 'mixed\n'
      ;;
  esac
}

record_inventory() {
  local status="$1"
  local repo_rel="$2"
  local kind="$3"
  local code_file_count="$4"
  local sample_files="$5"
  local reason="$6"

  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(sanitize_field "${status}")" \
    "$(sanitize_field "${repo_rel}")" \
    "$(sanitize_field "${kind}")" \
    "$(sanitize_field "${code_file_count}")" \
    "$(sanitize_field "${sample_files}")" \
    "$(sanitize_field "${reason}")" \
    >> "${INVENTORY_FILE}"
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
LOCK_FILE="${LOG_DIR}/bug-sweep-agentic.lock"
exec 9>"${LOCK_FILE}"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || {
    printf 'info: another bug_sweep_agentic.sh run is already active\n'
    exit 0
  }
fi

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_DIR="${LOG_DIR}/${TIMESTAMP}"
LOG_FILE="${RUN_DIR}/run.log"
INVENTORY_FILE="${RUN_DIR}/inventory.tsv"
CANDIDATE_FILE="${RUN_DIR}/candidates.tsv"
PROMPT_RUNTIME_FILE="${RUN_DIR}/prompt.txt"
AGENT_OUTPUT_FILE="${RUN_DIR}/agent-output.txt"
LAST_MESSAGE_FILE="${RUN_DIR}/last-message.txt"
LATEST_LOG_LINK="${LOG_DIR}/latest.log"
LATEST_OUTPUT_LINK="${LOG_DIR}/latest-output.txt"
LATEST_INVENTORY_LINK="${LOG_DIR}/latest-inventory.tsv"

mkdir -p "${RUN_DIR}"

printf 'status\trepo_rel\tkind\tcode_file_count\tsample_files\treason\n' > "${INVENTORY_FILE}"

log "=== bug sweep agentic run ==="
log "repo root       : ${REPO_ROOT}"
log "portfolio root  : ${PORTFOLIO_ROOT}"
log "provider req    : ${PROVIDER_REQUESTED}"
log "model override  : ${MODEL_REQUESTED:-<default>}"
log "prompt file     : ${PROMPT_FILE}"
log "run dir         : ${RUN_DIR}"
log ""

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

candidate_count=0
dirty_count=0
skip_count=0

for repo in "${REPO_DIRS[@]}"; do
  local_rel="${repo#${PORTFOLIO_ROOT}/}"
  log "scan repo       : ${local_rel}"

  if (( FORCE == 0 )) && [[ -n "$(git -C "${repo}" status --porcelain 2>/dev/null || true)" ]]; then
    record_inventory "dirty" "${local_rel}" "-" "0" "-" "worktree not clean"
    dirty_count=$(( dirty_count + 1 ))
    continue
  fi

  mapfile -t code_files < <(git -C "${repo}" ls-files -- "${CODE_PATTERNS[@]}" 2>/dev/null || true)
  if (( ${#code_files[@]} == 0 )); then
    record_inventory "skip" "${local_rel}" "none" "0" "-" "no tracked source files matched the bug-sweep patterns"
    skip_count=$(( skip_count + 1 ))
    continue
  fi

  kind="$(infer_code_kind "${code_files[@]}")"
  sample=("${code_files[@]:0:5}")
  sample_files="$(join_by '; ' "${sample[@]}")"
  record_inventory \
    "candidate" \
    "${local_rel}" \
    "${kind}" \
    "${#code_files[@]}" \
    "${sample_files}" \
    "clean code-focused repo"
  candidate_count=$(( candidate_count + 1 ))
done

ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"
ln -sf "${INVENTORY_FILE}" "${LATEST_INVENTORY_LINK}"

awk -F '\t' 'NR == 1 || $1 == "candidate"' "${INVENTORY_FILE}" > "${CANDIDATE_FILE}"

log "inventory summary: candidate=${candidate_count} dirty=${dirty_count} skipped=${skip_count}"

if (( candidate_count == 0 )); then
  log "no clean code repos matched the bug-sweep inventory"
  exit 0
fi

PROVIDER="$(agentic_resolve_provider "${PROVIDER_REQUESTED}" "${MODEL_REQUESTED}")" \
  || fail "no ready agent provider found (codex, claude, copilot)"
PROMPT_TEXT="$(cat "${PROMPT_FILE}")"

log "provider used   : ${PROVIDER}"
log "provider probe  : auth/status + model readiness"

{
  printf '%s\n\n' "${PROMPT_TEXT}"
  printf 'Candidate inventory (%s):\n' "${CANDIDATE_FILE}"
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
ln -sf "${AGENT_OUTPUT_FILE}" "${LATEST_OUTPUT_LINK}"

if (( AGENT_STATUS != 0 )); then
  log "agent exit status: ${AGENT_STATUS}"
  exit "${AGENT_STATUS}"
fi

log "agent exit status: 0"
