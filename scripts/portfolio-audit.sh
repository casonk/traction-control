#!/usr/bin/env bash
# portfolio-audit.sh — daily governance audit across the portfolio
#
# Scans every git repository under PORTFOLIO_ROOT and reports:
#   - missing Tier-1 baseline files (README, LICENSE, AGENTS.md, BACKLOG.md, etc.)
#   - missing CHATHISTORY.md entry in .gitignore
#   - missing AGENTS.md sudo-boundary guidance
#   - missing .pre-commit-config.yaml in non-doc code repos
#   - SECURITY.md files that exist but miss portfolio best-practice guidance
#
# Exit code 0 = everything clean; 1 = gaps found; 2 = setup error.
# Logs are written to LOG_DIR (default: ~/.local/share/portfolio-audit/).
# Run manually to verify: bash /path/to/portfolio-audit.sh

set -euo pipefail

# ── configuration ────────────────────────────────────────────────────────────
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-/mnt/4tb-m2/git}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/portfolio-audit}"
MAX_DEPTH=4
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ─────────────────────────────────────────────────────────────────────────────

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"
LATEST_LINK="${LOG_DIR}/latest.log"

mkdir -p "${LOG_DIR}"

log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
warn() { echo "[$(date '+%H:%M:%S')] WARN  $*" | tee -a "${LOG_FILE}"; }

TIER1_FILES=(
    README.md
    LICENSE
    .gitignore
    AGENTS.md
    LESSONSLEARNED.md
    BACKLOG.md
    .editorconfig
    SECURITY.md
    CODE_OF_CONDUCT.md
    CHANGELOG.md
    CONTRIBUTING.md
    docs/contributor-architecture-blueprint.md
    docs/diagrams/repo-architecture.puml
    docs/diagrams/repo-architecture.drawio
    .github/PULL_REQUEST_TEMPLATE.md
    .github/ISSUE_TEMPLATE/bug_report.md
    .github/ISSUE_TEMPLATE/feature_request.md
)

log "=== portfolio-audit daily run ==="
log "portfolio root : ${PORTFOLIO_ROOT}"
log "log file       : ${LOG_FILE}"
log ""

# ── discover repos ────────────────────────────────────────────────────────────
mapfile -t REPO_DIRS < <(
    find "${PORTFOLIO_ROOT}" \
        -maxdepth "${MAX_DEPTH}" \
        -type d \
        -name ".git" \
        ! -path "*/archive-repos/*" \
        ! -path "*/vendor/filebrowser-upstream/*" \
    | sed 's|/.git$||' \
    | sort
)

log "found ${#REPO_DIRS[@]} repositories"
log ""

# ── audit ─────────────────────────────────────────────────────────────────────
GAP_COUNT=0
AGENTS_SUDO_MARKER='Agents will never be able to run `sudo` commands'

for repo in "${REPO_DIRS[@]}"; do
    rel="${repo#${PORTFOLIO_ROOT}/}"
    missing=()

    # Tier-1 baseline files
    for f in "${TIER1_FILES[@]}"; do
        [[ ! -f "${repo}/${f}" ]] && missing+=("$f")
    done

    # AGENTS.md must include the standard sudo handoff boundary.
    if [[ -f "${repo}/AGENTS.md" ]] && ! grep -qF "${AGENTS_SUDO_MARKER}" "${repo}/AGENTS.md"; then
        missing+=("AGENTS.md missing sudo boundary")
    fi

    # CHATHISTORY.md must be gitignored
    if ! grep -q "CHATHISTORY.md" "${repo}/.gitignore" 2>/dev/null; then
        missing+=(".gitignore missing CHATHISTORY.md")
    fi

    # .pre-commit-config.yaml for non-doc repos
    if [[ ! "$rel" =~ ^doc-repos ]] && [[ ! -f "${repo}/.pre-commit-config.yaml" ]]; then
        missing+=(".pre-commit-config.yaml")
    fi

    # SECURITY.md best-practice content checks
    security_status=0
    set +e
    security_output="$(
        python3 "${SCRIPT_DIR}/check_security_md.py" \
            --repo "${repo}" \
            --repo-rel "${rel}" 2>&1
    )"
    security_status=$?
    set -e

    case "${security_status}" in
        0)
            ;;
        1)
            while IFS= read -r line; do
                [[ -n "${line}" ]] && missing+=("SECURITY.md policy: ${line}")
            done <<< "${security_output}"
            ;;
        *)
            warn "${rel}: SECURITY.md checker error"
            while IFS= read -r line; do
                [[ -n "${line}" ]] && warn "  checker: ${line}"
            done <<< "${security_output}"
            exit 2
            ;;
    esac

    if (( ${#missing[@]} > 0 )); then
        warn "${rel}: ${#missing[@]} gap(s)"
        for m in "${missing[@]}"; do
            warn "  missing: ${m}"
        done
        GAP_COUNT=$(( GAP_COUNT + ${#missing[@]} ))
    else
        log "OK  ${rel}"
    fi
done

log ""
log "=== done ==="
log "repos scanned  : ${#REPO_DIRS[@]}"
log "total gaps     : ${GAP_COUNT}"
log ""

# keep a stable symlink to the most recent log
ln -sf "${LOG_FILE}" "${LATEST_LINK}"

if (( GAP_COUNT > 0 )); then
    log "ACTION REQUIRED: open ${LATEST_LINK} and apply fixes in traction-control"
    exit 1
fi
exit 0
