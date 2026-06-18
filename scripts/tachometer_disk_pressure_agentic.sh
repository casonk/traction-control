#!/usr/bin/env bash
# tachometer_disk_pressure_agentic.sh — launch an unattended remediation agent
# only when tachometer reports disk pressure in clean local portfolio repos.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
PROMPT_FILE_DEFAULT="${REPO_ROOT}/config/prompts/tachometer-disk-pressure-agentic.md"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/tachometer-disk-pressure-agentic}"
PROMPT_FILE="${PROMPT_FILE:-${PROMPT_FILE_DEFAULT}}"
PROVIDER_REQUESTED="${TACHOMETER_DISK_PRESSURE_AGENTIC_PROVIDER:-auto}"
MODEL_REQUESTED="${TACHOMETER_DISK_PRESSURE_AGENTIC_MODEL:-}"
MAX_DEPTH=4
FORCE=0
DRY_RUN=0
PRESSURE_THRESHOLD_PERCENT="${TACHOMETER_DISK_PRESSURE_PERCENT:-90}"
CANDIDATE_MIN_SIZE_MIB="${TACHOMETER_DISK_PRESSURE_MIN_SIZE_MIB:-250}"
MAX_CANDIDATES="${TACHOMETER_DISK_PRESSURE_MAX_CANDIDATES:-8}"

source "${REPO_ROOT}/scripts/lib/agentic_provider.sh"

usage() {
  cat <<EOF
Usage: tachometer_disk_pressure_agentic.sh [options]

Scan tachometer backlog/summary files across the local portfolio. If disk
pressure is present, invoke an unattended agent to implement conservative
repo-local archive automation in clean candidate repos.

Options:
  --provider NAME        Provider to use: auto, codex, claude, or copilot.
  --model MODEL          Optional model override for the selected provider.
  --portfolio-root PATH  Override the portfolio scan root.
  --log-dir DIR          Override the run-log directory.
  --prompt-file PATH     Override the remediation prompt file.
  --threshold-percent N  Disk utilization percent that counts as pressure.
  --min-size-mib N       Minimum repo size to hand to the agent.
  --max-candidates N     Maximum candidate repos handed to the agent.
  --force                Ignore dirty worktrees and include them if they match.
  --dry-run              Inventory only; do not launch an agent.
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

record_inventory() {
  local status="$1"
  local repo_rel="$2"
  local size_mib="$3"
  local disk_pct="$4"
  local pressure_source="$5"
  local reason="$6"

  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(sanitize_field "${status}")" \
    "$(sanitize_field "${repo_rel}")" \
    "$(sanitize_field "${size_mib}")" \
    "$(sanitize_field "${disk_pct}")" \
    "$(sanitize_field "${pressure_source}")" \
    "$(sanitize_field "${reason}")" \
    >> "${INVENTORY_FILE}"
}

repo_probe() {
  local repo="$1"
  python3 - "$repo" "$PRESSURE_THRESHOLD_PERCENT" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
threshold = float(sys.argv[2])
sources: list[str] = []
max_pct: float | None = None

for rel, label in (
    (".tachometer/backlog.json", "system.backlog"),
    (".tachometer/host-backlog.json", "host.backlog"),
):
    path = repo / rel
    if not path.exists():
        continue
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if not isinstance(entries, list):
        continue
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "open" and entry.get("light_key") == "disk":
            sources.append(f"{label}:{entry.get('id', 'disk')}")

for rel, label in (
    (".tachometer/summary.json", "system.summary"),
    (".tachometer/host-summary.json", "host.summary"),
):
    path = repo / rel
    if not path.exists():
        continue
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    used = summary.get("avg_disk_used_bytes")
    total = summary.get("latest_disk_total_bytes")
    if not isinstance(used, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
        continue
    pct = float(used) / float(total) * 100.0
    max_pct = pct if max_pct is None else max(max_pct, pct)
    if pct >= threshold:
        sources.append(f"{label}:{pct:.1f}%")

disk_pct = "-" if max_pct is None else f"{max_pct:.1f}"
pressure_source = ";".join(sources) if sources else "-"
pressure_hit = "1" if sources else "0"
host_pressure = "1" if any(source.startswith("host.") for source in sources) else "0"
print(f"{disk_pct}\t{pressure_source}\t{pressure_hit}\t{host_pressure}")
PY
}

repo_size_mib() {
  local repo="$1"
  local size_kib
  size_kib="$(du -sk --exclude=.git --exclude=.storage-archives "$repo" 2>/dev/null | awk '{print $1}')"
  if [[ -z "${size_kib}" ]]; then
    printf '0\n'
    return
  fi
  printf '%s\n' "$(( (size_kib + 1023) / 1024 ))"
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
    --threshold-percent)
      PRESSURE_THRESHOLD_PERCENT="$2"
      shift 2
      ;;
    --min-size-mib)
      CANDIDATE_MIN_SIZE_MIB="$2"
      shift 2
      ;;
    --max-candidates)
      MAX_CANDIDATES="$2"
      shift 2
      ;;
    --force)
      FORCE=1
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

case "${PROVIDER_REQUESTED}" in
  auto|codex|claude|copilot)
    ;;
  *)
    fail "unsupported provider: ${PROVIDER_REQUESTED}"
    ;;
esac

command -v git >/dev/null 2>&1 || fail "git not found"
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
[[ -d "${PORTFOLIO_ROOT}" ]] || fail "portfolio root does not exist: ${PORTFOLIO_ROOT}"
[[ -f "${PROMPT_FILE}" ]] || fail "missing prompt file: ${PROMPT_FILE}"

mkdir -p "${LOG_DIR}"
LOCK_FILE="${LOG_DIR}/tachometer-disk-pressure-agentic.lock"
exec 9>"${LOCK_FILE}"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || {
    printf 'info: another tachometer_disk_pressure_agentic.sh run is already active\n'
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

printf 'status\trepo_rel\tsize_mib\tdisk_pct\tpressure_source\treason\n' > "${INVENTORY_FILE}"
printf 'status\trepo_rel\tsize_mib\tdisk_pct\tpressure_source\treason\n' > "${CANDIDATE_FILE}"

log "=== tachometer disk-pressure agentic run ==="
log "repo root       : ${REPO_ROOT}"
log "portfolio root  : ${PORTFOLIO_ROOT}"
log "provider req    : ${PROVIDER_REQUESTED}"
log "model override  : ${MODEL_REQUESTED:-<default>}"
log "prompt file     : ${PROMPT_FILE}"
log "threshold pct   : ${PRESSURE_THRESHOLD_PERCENT}"
log "min size MiB    : ${CANDIDATE_MIN_SIZE_MIB}"
log "max candidates  : ${MAX_CANDIDATES}"
log "dry run         : ${DRY_RUN}"
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

dirty_count=0
skip_count=0
pressure_count=0
host_pressure_count=0
clean_count=0

for repo in "${REPO_DIRS[@]}"; do
  repo_rel="${repo#${PORTFOLIO_ROOT}/}"
  log "scan repo       : ${repo_rel}"

  if [[ ! -f "${repo}/config/tachometer/profile.toml" ]] \
    && [[ ! -d "${repo}/.tachometer" ]]; then
    record_inventory "skip" "${repo_rel}" "0" "-" "-" "no tachometer manifest or local tachometer data"
    skip_count=$(( skip_count + 1 ))
    continue
  fi

  if (( FORCE == 0 )) && [[ -n "$(git -C "${repo}" status --porcelain 2>/dev/null || true)" ]]; then
    record_inventory "dirty" "${repo_rel}" "0" "-" "-" "worktree not clean"
    dirty_count=$(( dirty_count + 1 ))
    continue
  fi

  probe="$(repo_probe "${repo}")"
  IFS=$'\t' read -r disk_pct pressure_source pressure_hit host_pressure_hit <<< "${probe}"
  size_mib="$(repo_size_mib "${repo}")"
  clean_count=$(( clean_count + 1 ))

  if [[ "${pressure_hit}" == "1" ]]; then
    record_inventory "pressure" "${repo_rel}" "${size_mib}" "${disk_pct}" "${pressure_source}" "tachometer disk pressure detected"
    pressure_count=$(( pressure_count + 1 ))
    if [[ "${host_pressure_hit}" == "1" ]]; then
      host_pressure_count=$(( host_pressure_count + 1 ))
    fi
  else
    record_inventory "clean" "${repo_rel}" "${size_mib}" "${disk_pct}" "-" "no disk pressure detected"
  fi
done

ln -sf "${LOG_FILE}" "${LATEST_LOG_LINK}"
ln -sf "${INVENTORY_FILE}" "${LATEST_INVENTORY_LINK}"

TAB="$(printf '\t')"
if (( pressure_count > 0 )); then
  awk -F '\t' -v OFS='\t' -v min="${CANDIDATE_MIN_SIZE_MIB}" \
    'NR > 1 && $1 == "pressure" && ($3 + 0) >= min {$1 = "candidate"; print}' \
    "${INVENTORY_FILE}" \
    | sort -t "${TAB}" -k3,3nr \
    | head -n "${MAX_CANDIDATES}" \
    >> "${CANDIDATE_FILE}"
fi

candidate_count="$(awk -F '\t' 'NR > 1 && $1 == "candidate" {count += 1} END {print count + 0}' "${CANDIDATE_FILE}")"
if (( candidate_count == 0 && host_pressure_count > 0 )); then
  awk -F '\t' -v OFS='\t' -v min="${CANDIDATE_MIN_SIZE_MIB}" \
    'NR > 1 && $1 == "clean" && ($3 + 0) >= min {$1 = "candidate"; $6 = "host disk pressure; selected by local repo size"; print}' \
    "${INVENTORY_FILE}" \
    | sort -t "${TAB}" -k3,3nr \
    | head -n "${MAX_CANDIDATES}" \
    >> "${CANDIDATE_FILE}"
  candidate_count="$(awk -F '\t' 'NR > 1 && $1 == "candidate" {count += 1} END {print count + 0}' "${CANDIDATE_FILE}")"
fi

log "inventory summary: clean=${clean_count} pressure=${pressure_count} host_pressure=${host_pressure_count} dirty=${dirty_count} skipped=${skip_count} candidate=${candidate_count}"

if (( pressure_count == 0 && host_pressure_count == 0 )); then
  log "no tachometer disk pressure detected; agent not launched"
  exit 0
fi

if (( candidate_count == 0 )); then
  log "disk pressure detected, but no clean repo met candidate selection thresholds"
  exit 0
fi

if (( DRY_RUN == 1 )); then
  log "dry-run complete; agent not launched"
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
