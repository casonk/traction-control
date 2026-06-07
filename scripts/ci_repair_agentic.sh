#!/usr/bin/env bash
# ci_repair_agentic.sh — unattended GitHub Actions CI discovery and repair pass
# across clean portfolio repos.
#
# The wrapper inventories the latest default-branch workflow runs across local
# GitHub repos, skips dirty worktrees by default, and only invokes an agent CLI
# when one or more repos have currently failing CI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
PROMPT_FILE_DEFAULT="${REPO_ROOT}/config/prompts/ci-repair-agentic.md"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/ci-repair-agentic}"
PROMPT_FILE="${PROMPT_FILE:-${PROMPT_FILE_DEFAULT}}"
PROVIDER_REQUESTED="${CI_REPAIR_AGENTIC_PROVIDER:-auto}"
MODEL_REQUESTED="${CI_REPAIR_AGENTIC_MODEL:-}"
MAX_DEPTH=4
RUN_LIMIT=30
FORCE=0

source "${REPO_ROOT}/scripts/lib/agentic_provider.sh"

usage() {
  cat <<EOF
Usage: ci_repair_agentic.sh [options]

Scan the portfolio for failing GitHub Actions CI on the latest default-branch
commit of each clean GitHub repo, then invoke an unattended agent to repair the
affected repos.

Options:
  --provider NAME        Provider to use: auto, codex, claude, or copilot.
  --model MODEL          Optional model override for the selected provider.
  --portfolio-root PATH  Override the portfolio scan root.
  --log-dir DIR          Override the run-log directory.
  --prompt-file PATH     Override the maintenance prompt file.
  --run-limit N          How many recent branch workflow runs to inspect per repo (default: 30).
  --force                Ignore dirty worktrees and scan them anyway.
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
    --run-limit)
      RUN_LIMIT="$2"
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

[[ "${RUN_LIMIT}" =~ ^[0-9]+$ ]] || fail "--run-limit must be a non-negative integer"
command -v git >/dev/null 2>&1 || fail "git not found"
command -v gh >/dev/null 2>&1 || fail "gh not found"
command -v jq >/dev/null 2>&1 || fail "jq not found"
gh auth status >/dev/null 2>&1 || fail "gh must be authenticated before this task can run"
[[ -d "${PORTFOLIO_ROOT}" ]] || fail "portfolio root does not exist: ${PORTFOLIO_ROOT}"
[[ -f "${PROMPT_FILE}" ]] || fail "missing prompt file: ${PROMPT_FILE}"

mkdir -p "${LOG_DIR}"
LOCK_FILE="${LOG_DIR}/ci-repair-agentic.lock"
exec 9>"${LOCK_FILE}"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || {
    printf 'info: another ci_repair_agentic.sh run is already active\n'
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

parse_github_slug() {
  local remote_url="$1"

  case "${remote_url}" in
    git@github.com:*)
      remote_url="${remote_url#git@github.com:}"
      ;;
    https://github.com/*)
      remote_url="${remote_url#https://github.com/}"
      ;;
    ssh://git@github.com/*)
      remote_url="${remote_url#ssh://git@github.com/}"
      ;;
    *)
      return 1
      ;;
  esac

  remote_url="${remote_url%.git}"
  [[ -n "${remote_url}" ]] || return 1
  printf '%s\n' "${remote_url}"
}

default_branch_for_repo() {
  local repo="$1"
  local default_ref=""

  default_ref="$(git -C "${repo}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [[ -n "${default_ref}" ]]; then
    printf '%s\n' "${default_ref#origin/}"
    return 0
  fi

  if git -C "${repo}" show-ref --verify --quiet refs/remotes/origin/main \
    || git -C "${repo}" show-ref --verify --quiet refs/heads/main; then
    printf 'main\n'
    return 0
  fi

  if git -C "${repo}" show-ref --verify --quiet refs/remotes/origin/master \
    || git -C "${repo}" show-ref --verify --quiet refs/heads/master; then
    printf 'master\n'
    return 0
  fi

  return 1
}

record_inventory() {
  local status="$1"
  local repo_rel="$2"
  local repo_slug="$3"
  local branch="$4"
  local head_sha="$5"
  local run_ids="$6"
  local workflows="$7"
  local urls="$8"
  local reason="$9"

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(sanitize_field "${status}")" \
    "$(sanitize_field "${repo_rel}")" \
    "$(sanitize_field "${repo_slug}")" \
    "$(sanitize_field "${branch}")" \
    "$(sanitize_field "${head_sha}")" \
    "$(sanitize_field "${run_ids}")" \
    "$(sanitize_field "${workflows}")" \
    "$(sanitize_field "${urls}")" \
    "$(sanitize_field "${reason}")" \
    >> "${INVENTORY_FILE}"
}

PROMPT_TEXT="$(cat "${PROMPT_FILE}")"

printf 'status\trepo_rel\trepo_slug\tbranch\thead_sha\trun_ids\tworkflows\turls\treason\n' > "${INVENTORY_FILE}"

log "=== CI repair agentic run ==="
log "repo root       : ${REPO_ROOT}"
log "portfolio root  : ${PORTFOLIO_ROOT}"
log "provider req    : ${PROVIDER_REQUESTED}"
log "model override  : ${MODEL_REQUESTED:-<default>}"
log "provider probe  : auth/status + model readiness"
log "prompt file     : ${PROMPT_FILE}"
log "run limit       : ${RUN_LIMIT}"
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

candidate_count=0
clean_count=0
dirty_count=0
pending_count=0
no_ci_count=0
skipped_count=0
error_count=0

for repo in "${REPO_DIRS[@]}"; do
  repo_rel="${repo#${PORTFOLIO_ROOT}/}"
  log "scan repo       : ${repo_rel}"

  if (( FORCE == 0 )) && [[ -n "$(git -C "${repo}" status --porcelain 2>/dev/null || true)" ]]; then
    record_inventory "dirty" "${repo_rel}" "-" "-" "-" "-" "-" "-" "worktree not clean"
    dirty_count=$(( dirty_count + 1 ))
    continue
  fi

  origin_url="$(git -C "${repo}" remote get-url origin 2>/dev/null || true)"
  if ! repo_slug="$(parse_github_slug "${origin_url}")"; then
    record_inventory "skip" "${repo_rel}" "-" "-" "-" "-" "-" "-" "no GitHub origin remote"
    skipped_count=$(( skipped_count + 1 ))
    continue
  fi

  if ! branch="$(default_branch_for_repo "${repo}")"; then
    record_inventory "skip" "${repo_rel}" "${repo_slug}" "-" "-" "-" "-" "-" "default branch unknown"
    skipped_count=$(( skipped_count + 1 ))
    continue
  fi

  if ! runs_json="$(gh run list \
    --repo "${repo_slug}" \
    --branch "${branch}" \
    --limit "${RUN_LIMIT}" \
    --json databaseId,headSha,status,conclusion,url,workflowName,createdAt,event 2>&1)"; then
    record_inventory "error" "${repo_rel}" "${repo_slug}" "${branch}" "-" "-" "-" "-" "${runs_json}"
    error_count=$(( error_count + 1 ))
    continue
  fi

  head_sha="$(printf '%s' "${runs_json}" | jq -r '.[0].headSha // empty')"
  if [[ -z "${head_sha}" ]]; then
    record_inventory "no_ci" "${repo_rel}" "${repo_slug}" "${branch}" "-" "-" "-" "-" "no default-branch workflow runs found"
    no_ci_count=$(( no_ci_count + 1 ))
    continue
  fi

  latest_sha_runs="$(
    printf '%s' "${runs_json}" \
      | jq -c --arg sha "${head_sha}" '[.[] | select(.headSha == $sha)]'
  )"

  current_pending_count="$(
    printf '%s' "${latest_sha_runs}" | jq 'map(select(.status != "completed")) | length'
  )"
  if (( current_pending_count > 0 )); then
    pending_urls="$(
      printf '%s' "${latest_sha_runs}" | jq -r '[.[].url] | join(", ")'
    )"
    record_inventory \
      "pending" \
      "${repo_rel}" \
      "${repo_slug}" \
      "${branch}" \
      "${head_sha}" \
      "-" \
      "-" \
      "${pending_urls}" \
      "latest default-branch workflow run still in progress"
    pending_count=$(( pending_count + 1 ))
    continue
  fi

  failing_runs="$(
    printf '%s' "${latest_sha_runs}" \
      | jq -c '[.[] | select(((.conclusion // "") as $c | ["failure", "timed_out", "action_required", "startup_failure", "stale"] | index($c)))]'
  )"
  failing_count="$(printf '%s' "${failing_runs}" | jq 'length')"

  if (( failing_count > 0 )); then
    run_ids="$(printf '%s' "${failing_runs}" | jq -r '[.[].databaseId | tostring] | join(", ")')"
    workflows="$(printf '%s' "${failing_runs}" | jq -r '[.[].workflowName] | join("; ")')"
    urls="$(printf '%s' "${failing_runs}" | jq -r '[.[].url] | join(", ")')"
    record_inventory \
      "candidate" \
      "${repo_rel}" \
      "${repo_slug}" \
      "${branch}" \
      "${head_sha}" \
      "${run_ids}" \
      "${workflows}" \
      "${urls}" \
      "latest default-branch CI is failing"
    candidate_count=$(( candidate_count + 1 ))
    continue
  fi

  record_inventory \
    "clean" \
    "${repo_rel}" \
    "${repo_slug}" \
    "${branch}" \
    "${head_sha}" \
    "-" \
    "-" \
    "-" \
    "latest default-branch CI is green or non-actionable"
  clean_count=$(( clean_count + 1 ))
done

ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"
ln -sf "${INVENTORY_FILE}" "${LATEST_INVENTORY_LINK}"

awk -F '\t' 'NR == 1 || $1 == "candidate"' "${INVENTORY_FILE}" > "${CANDIDATE_FILE}"

log "inventory summary: candidate=${candidate_count} clean=${clean_count} dirty=${dirty_count} pending=${pending_count} no_ci=${no_ci_count} skipped=${skipped_count} error=${error_count}"

if (( candidate_count == 0 )); then
  log "no failing default-branch CI detected in clean GitHub repos"
  exit 0
fi

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
ln -sf "${AGENT_OUTPUT_FILE}" "${LATEST_OUTPUT_LINK}"

if (( AGENT_STATUS != 0 )); then
  log "agent exit status: ${AGENT_STATUS}"
  exit "${AGENT_STATUS}"
fi

log "agent exit status: 0"
