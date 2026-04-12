#!/usr/bin/env bash
# archility-daily.sh — daily architecture audit across the portfolio
#
# Discovers all git repos under PORTFOLIO_ROOT and runs:
#   archility audit  — drift report for every repo
#
# Diagram renders stay in the twice-weekly pass (archility-weekly.sh).
# Logs are written to LOG_DIR (default: ~/.local/share/archility-daily/).
# Run once manually to verify: bash /path/to/archility-daily.sh

set -euo pipefail

# ── configuration ────────────────────────────────────────────────────────────
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-/mnt/4tb-m2/git}"
ARCHILITY_CMD="${ARCHILITY_CMD:-archility}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/archility-daily}"
MAX_DEPTH=4   # how deep to search for .git dirs below PORTFOLIO_ROOT
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"

mkdir -p "${LOG_DIR}"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

log "=== archility daily run ==="
log "portfolio root : ${PORTFOLIO_ROOT}"
log "archility      : $(command -v "${ARCHILITY_CMD}" || echo 'not found')"
log "log file       : ${LOG_FILE}"
log ""

# ── discover repos ───────────────────────────────────────────────────────────
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
log ""

# ── audit all repos ───────────────────────────────────────────────────────────
log "--- AUDIT ---"
AUDIT_FAIL=0
"${ARCHILITY_CMD}" audit "${REPO_DIRS[@]}" 2>&1 | tee -a "${LOG_FILE}" || AUDIT_FAIL=$?
if [[ $AUDIT_FAIL -ne 0 ]]; then
    log "WARNING: archility audit exited with code ${AUDIT_FAIL}"
fi
log ""

# ── summary ───────────────────────────────────────────────────────────────────
log "=== done ==="
log "repos audited  : ${#REPO_DIRS[@]}"
log ""

# keep a stable symlink to the most recent log
ln -sf "${LOG_FILE}" "${LATEST_LINK}"

exit $(( AUDIT_FAIL > 0 ? 1 : 0 ))
