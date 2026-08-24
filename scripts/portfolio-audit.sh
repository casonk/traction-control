#!/usr/bin/env bash
# portfolio-audit.sh — daily governance audit across the portfolio
#
# Scans every git repository under PORTFOLIO_ROOT and reports:
#   - missing Tier-1 baseline files (README, LICENSE, AGENTS.md, BACKLOG.md, etc.)
#   - missing CHATHISTORY.md entry in .gitignore
#   - missing .pre-commit-config.yaml in non-doc code repos
#   - AGENTS.md files that exist but miss the shared agent conventions
#     (sudo boundary, portfolio standards backlink, session-memory boundary,
#     local CI verification) — see check_agents_md.py
#   - SECURITY.md files that exist but miss portfolio best-practice guidance
#
# Exit code 0 = everything clean; 1 = gaps found; 2 = setup error.
# Logs are written to LOG_DIR (default: ~/.local/share/portfolio-audit/).
# Run manually to verify: bash /path/to/portfolio-audit.sh

set -euo pipefail

# ── configuration ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-$(cd "${SCRIPT_DIR}/../../.." && pwd)}"
LOG_DIR="${LOG_DIR:-${HOME}/.local/share/portfolio-audit}"
MAX_DEPTH=4
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
)

# GitHub serves these from the account-level `.github` repository to any repo
# that lacks its own, private repos included (verified against a private repo
# carrying neither file). A repo without a local copy is therefore already
# covered, so requiring one here would report gaps that are closed — the same
# cry-wolf failure the concept-based AGENTS.md rewrite was meant to end.
INHERITABLE_FILES=(
    .github/PULL_REQUEST_TEMPLATE.md
    .github/ISSUE_TEMPLATE/bug_report.md
    .github/ISSUE_TEMPLATE/feature_request.md
)

# Locate the account `.github` checkout by its remote rather than by path, so
# this keeps working if the directory is renamed or moved.
DEFAULTS_REPO=""
while IFS= read -r candidate; do
    remote="$(git -C "${candidate}" remote get-url origin 2>/dev/null || true)"
    case "${remote}" in
        */.github|*/.github.git) DEFAULTS_REPO="${candidate}"; break ;;
    esac
done < <(
    find "${PORTFOLIO_ROOT}" -maxdepth 4 -type d -name .git \
        ! -path "*/archive-repos/*" 2>/dev/null | sed 's|/.git$||' | sort
)

# Present in the defaults repo at any of the three locations GitHub accepts.
defaults_provide() {  # <relative-path>
    local rel="$1"
    [[ -n "${DEFAULTS_REPO}" ]] || return 1
    local bare="${rel#.github/}"
    [[ -e "${DEFAULTS_REPO}/${rel}" ]] && return 0
    [[ -e "${DEFAULTS_REPO}/${bare}" ]] && return 0
    [[ -e "${DEFAULTS_REPO}/docs/${bare}" ]] && return 0
    return 1
}

log "=== portfolio-audit daily run ==="
log "portfolio root : ${PORTFOLIO_ROOT}"
log "log file       : ${LOG_FILE}"
log ""

# ── discover repos ────────────────────────────────────────────────────────────
REPO_DIRS=()
while IFS= read -r repo_dir; do
    REPO_DIRS+=("${repo_dir}")
done < <(
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
if [[ -n "${DEFAULTS_REPO}" ]]; then
    log "account defaults : ${DEFAULTS_REPO#${PORTFOLIO_ROOT}/}"
else
    warn "account defaults : none found — community-health files will be required per repo"
fi
log ""

# ── audit ─────────────────────────────────────────────────────────────────────
GAP_COUNT=0

repo_index=0
while (( repo_index < ${#REPO_DIRS[@]} )); do
    repo="${REPO_DIRS[$repo_index]}"
    rel="${repo#${PORTFOLIO_ROOT}/}"
    missing=()

    # Tier-1 baseline files
    for f in "${TIER1_FILES[@]}"; do
        [[ ! -f "${repo}/${f}" ]] && missing+=("$f")
    done

    # Tier-1 files that may instead be inherited from the account `.github`
    # repository. Only a gap when neither the repo nor the defaults repo has it.
    for f in "${INHERITABLE_FILES[@]}"; do
        if [[ ! -f "${repo}/${f}" ]] && ! defaults_provide "${f}"; then
            missing+=("$f (and no account-level default provides it)")
        fi
    done

    # AGENTS.md shared-convention checks (sudo boundary, portfolio standards
    # backlink, session-memory boundary, local CI verification). Concept-based,
    # not template-exact: the previous exact-string marker flagged three repos
    # that each carried a correct but differently-worded Sudo Boundary section.
    if [[ -f "${repo}/AGENTS.md" ]]; then
        agents_status=0
        set +e
        agents_output="$(
            python3 "${SCRIPT_DIR}/check_agents_md.py" \
                --repo "${repo}" \
                --repo-rel "${rel}" 2>&1
        )"
        agents_status=$?
        set -e

        case "${agents_status}" in
            0)
                ;;
            1)
                while IFS= read -r line; do
                    [[ -n "${line}" ]] && missing+=("AGENTS.md conventions: ${line}")
                done <<< "${agents_output}"
                ;;
            *)
                warn "${rel}: AGENTS.md checker error"
                while IFS= read -r line; do
                    [[ -n "${line}" ]] && warn "  checker: ${line}"
                done <<< "${agents_output}"
                exit 2
                ;;
        esac
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
    repo_index=$(( repo_index + 1 ))
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
