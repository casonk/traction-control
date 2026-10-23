#!/usr/bin/env bash
# archility-weekly.sh — twice-weekly architecture audit + diagram PRs across
# the registered portfolio
#
# Discovers all git repos under PORTFOLIO_ROOT, runs:
#   archility audit  — drift report for every repo
#   archility PR deploy — render eligible registered repos in isolated clones,
#                         then push diagram-only branches and open pull requests
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
PRIVATE_REGISTRY="${TRACTION_CONTROL_PRIVATE_REPOS_CONFIG:-${SCRIPT_DIR}/../config/repository-visibility/private.local.json}"
PUBLIC_REGISTRY="${TRACTION_CONTROL_PUBLIC_REPOS_CONFIG:-${SCRIPT_DIR}/../config/repository-visibility/public.local.json}"
PORTFOLIO_CATALOG="${TRACTION_CONTROL_PORTFOLIO_CATALOG:-${SCRIPT_DIR}/../config/portfolio/portfolio.local.json}"
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
        ! -path "*/archive-repos/*" -exec test -f "{}/HEAD" ';' -print \
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

# ── render registered repos in isolation and open diagram PRs ─────────────────
log "--- DIAGRAM PR DEPLOYMENT ---"
PR_FAIL=0
if [[ "${ARCHILITY_PR_DEPLOY:-1}" == "1" ]]; then
    python3 "${SCRIPT_DIR}/archility_pr_deploy.py" \
        --portfolio-root "${PORTFOLIO_ROOT}" \
        --private-registry "${PRIVATE_REGISTRY}" \
        --public-registry "${PUBLIC_REGISTRY}" \
        --catalog "${PORTFOLIO_CATALOG}" \
        --archility-command "${ARCHILITY_COMMAND[@]}" \
        2>&1 | tee -a "${LOG_FILE}" || PR_FAIL=$?
else
    log "diagram PR deployment disabled by ARCHILITY_PR_DEPLOY"
fi
if [[ $PR_FAIL -ne 0 ]]; then
    log "WARNING: diagram PR deployment exited with code ${PR_FAIL}"
fi
log ""

# ── summary ───────────────────────────────────────────────────────────────────
log "=== done ==="
log "repos audited  : ${#REPO_DIRS[@]}"
log "PR deploy error: ${PR_FAIL}"
log ""

# keep a stable symlink to the most recent log
ln -sf "${LOG_FILE}" "${LATEST_LINK}"

exit $(( AUDIT_FAIL + PR_FAIL > 0 ? 1 : 0 ))
