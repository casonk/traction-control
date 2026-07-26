#!/usr/bin/env bash
# archility-weekly.sh — twice-weekly architecture audit + render across the
# portfolio
#
# Discovers all git repos under PORTFOLIO_ROOT, runs:
#   archility audit  — drift report for every repo
#   archility render — refresh diagram renders for every repo
#
# Logs are written to LOG_DIR (default: ~/.local/share/archility-weekly/).
# Run once manually to verify: bash /path/to/archility-weekly.sh

set -euo pipefail

# ── configuration ────────────────────────────────────────────────────────────
# Derive the portfolio root from this script's location (scripts/ is three
# levels inside util-repos/traction-control which sits two levels inside the
# portfolio root), unless PORTFOLIO_ROOT is already set in the environment.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
ARCHILITY_CMD="${ARCHILITY_CMD:-archility}"
ARCHILITY_REPO_FALLBACK="${SCRIPT_DIR}/../../archility"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/archility-weekly}"
MAX_DEPTH=4   # how deep to search for .git dirs below PORTFOLIO_ROOT
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"

mkdir -p "${LOG_DIR}"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

if command -v "${ARCHILITY_CMD}" >/dev/null 2>&1; then
    ARCHILITY_COMMAND=("${ARCHILITY_CMD}")
elif [[ "${ARCHILITY_CMD}" == "archility" && -d "${ARCHILITY_REPO_FALLBACK}/src/archility" ]]; then
    export PYTHONPATH="${ARCHILITY_REPO_FALLBACK}/src${PYTHONPATH:+:${PYTHONPATH}}"
    ARCHILITY_COMMAND=(python3 -m archility)
else
    printf 'error: archility command not found: %s\n' "${ARCHILITY_CMD}" >&2
    exit 1
fi

log "=== archility twice-weekly run ==="
log "portfolio root : ${PORTFOLIO_ROOT}"
log "archility      : ${ARCHILITY_COMMAND[*]}"
log "log file       : ${LOG_FILE}"
log ""

# ── discover repos ───────────────────────────────────────────────────────────
REPO_DIRS=()
while IFS= read -r repo_dir; do
    REPO_DIRS+=("${repo_dir}")
done < <(
    find "${PORTFOLIO_ROOT}" \
        -maxdepth "${MAX_DEPTH}" \
        -type d \
        -name ".git" \
        ! -path "*/archive-repos/*" \
    | sed 's|/.git$||' \
    | sort
)

log "found ${#REPO_DIRS[@]} repositories"
log ""

if (( ${#REPO_DIRS[@]} == 0 )); then
    log "no repositories found; nothing to audit or render"
    exit 0
fi

# ── audit all repos ───────────────────────────────────────────────────────────
log "--- AUDIT ---"
AUDIT_FAIL=0
"${ARCHILITY_COMMAND[@]}" audit "${REPO_DIRS[@]}" 2>&1 | tee -a "${LOG_FILE}" || AUDIT_FAIL=$?
if [[ $AUDIT_FAIL -ne 0 ]]; then
    log "WARNING: archility audit exited with code ${AUDIT_FAIL}"
fi
log ""

# ── render each repo ─────────────────────────────────────────────────────────
log "--- RENDER ---"
RENDER_FAIL=0
for repo in "${REPO_DIRS[@]}"; do
    log "rendering: ${repo}"
    "${ARCHILITY_COMMAND[@]}" render "${repo}" 2>&1 | tee -a "${LOG_FILE}" || {
        log "WARNING: render failed for ${repo} (exit $?)"
        RENDER_FAIL=$(( RENDER_FAIL + 1 ))
    }
done
log ""

# ── summary ───────────────────────────────────────────────────────────────────
log "=== done ==="
log "repos audited  : ${#REPO_DIRS[@]}"
log "render failures: ${RENDER_FAIL}"
log ""

# keep a stable symlink to the most recent log
ln -sf "${LOG_FILE}" "${LATEST_LINK}"

exit $(( AUDIT_FAIL + RENDER_FAIL > 0 ? 1 : 0 ))
