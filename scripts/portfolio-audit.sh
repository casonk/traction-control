#!/usr/bin/env bash
# portfolio-audit.sh — daily governance audit across the portfolio
#
# Scans every git repository under PORTFOLIO_ROOT and reports:
#   - missing Tier-1 baseline files (README, LICENSE, AGENTS.md, BACKLOG.md, etc.)
#   - missing CHATHISTORY.md entry in .gitignore
#   - missing .pre-commit-config.yaml in non-doc code repos
#   - missing tracked baseline files, orphaned diagram renders, and tracked
#     local or runtime artifacts
#   - AGENTS.md files that exist but miss the shared agent conventions
#     (sudo boundary, portfolio standards backlink, session-memory boundary,
#     local CI verification) — see check_agents_md.py
#   - SECURITY.md files that exist but miss portfolio best-practice guidance
#   - GitHub descriptions in .github/repository.toml that are empty, open with
#     filler ("Repository for ..."), or are scaffold text (check_repo_description.py)
#   - local-model serving drift: crew-chief's serving policy against the models
#     actually pulled (crew-chief policy check --live)
#   - KeePass vault access: every vault auto-pass is configured for must be
#     readable and writable by this login (auto-pass check-access)
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
    CHANGELOG.md
    REFS-PUBLIC.md
    docs/contributor-architecture-blueprint.md
    docs/diagrams/repo-architecture.puml
    docs/diagrams/repo-architecture.drawio
)

# GitHub serves these from the account-level `.github` repository to any repo
# that lacks its own, private repos included (verified against private repos
# carrying neither file). A repo without a local copy is therefore already
# covered, so requiring one here would report gaps that are closed — the same
# cry-wolf failure the concept-based AGENTS.md rewrite was meant to end.
#
# CODE_OF_CONDUCT.md and CONTRIBUTING.md belong here for the same reason and
# were missed when this list was introduced, producing eight false positives
# across the four repos that carry neither. Seeding local copies instead would
# duplicate centrally-managed files and let them drift; inheritance satisfies
# the baseline rather than exempting a repo from it.
#
# SECURITY.md is deliberately NOT here. The account default would satisfy
# GitHub, but the repos that lack one are credential- and network-adjacent and
# need boundaries a generic policy cannot state, and check_security_md.py reads
# the local file.
INHERITABLE_FILES=(
    CODE_OF_CONDUCT.md
    CONTRIBUTING.md
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
        ! -path "*/archive-repos/*" -exec test -f "{}/HEAD" ';' -print 2>/dev/null | sed 's|/.git$||' | sort
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
        ! -path "*/vendor/filebrowser-upstream/*" -exec test -f "{}/HEAD" ';' -print \
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

    # One repo-wide AI contribution disclosure replaces per-commit attribution
    # (AGENTS.md rule 13). A repo without its own CONTRIBUTING.md inherits the
    # account default, which carries the same section.
    if [[ -f "${repo}/CONTRIBUTING.md" ]] \
        && ! grep -q '^## AI Contribution Disclosure' "${repo}/CONTRIBUTING.md"; then
        missing+=("CONTRIBUTING.md: AI Contribution Disclosure section")
    fi

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

    # GitHub description: flag only filler wording and scaffold text. Advisory
    # warnings are logged but do not count; whether the description still matches
    # the repository is the agentic REFS audit's review, not a regex's.
    description_status=0
    set +e
    description_output="$(
        python3 "${SCRIPT_DIR}/check_repo_description.py" \
            --repo "${repo}" \
            --repo-rel "${rel}" 2>&1
    )"
    description_status=$?
    set -e
    case "${description_status}" in
        0|1)
            while IFS= read -r line; do
                [[ -n "${line}" ]] || continue
                if [[ "${line}" == warning:* ]]; then
                    warn "${rel}: repository description ${line}"
                else
                    missing+=("repository description: ${line}")
                fi
            done <<< "${description_output}"
            ;;
        *)
            warn "${rel}: repository description checker error"
            while IFS= read -r line; do
                [[ -n "${line}" ]] && warn "  checker: ${line}"
            done <<< "${description_output}"
            exit 2
            ;;
    esac

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

    # The structure/artifact contract reads the Git index, so an untracked
    # scaffold cannot satisfy the baseline merely by existing on disk.
    quality_status=0
    set +e
    quality_output="$(
        PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/check_repository_quality.py" \
            --repo "${repo}" 2>&1
    )"
    quality_status=$?
    set -e
    case "${quality_status}" in
        0) ;;
        1)
            while IFS= read -r line; do
                [[ -n "${line}" ]] || continue
                # The worktree Tier-1 check already reports a wholly missing
                # file. Retain the index error when the file exists locally
                # but has not been staged/tracked.
                if [[ "${line}" == "quality check: required tracked file missing: "* ]]; then
                    required_path="${line#quality check: required tracked file missing: }"
                    [[ -f "${repo}/${required_path}" ]] || continue
                fi
                missing+=("${line}")
            done <<< "${quality_output}"
            ;;
        *)
            warn "${rel}: repository quality checker error"
            while IFS= read -r line; do
                [[ -n "${line}" ]] && warn "  checker: ${line}"
            done <<< "${quality_output}"
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

# ── private-name disclosure sweep ─────────────────────────────────────────────
# A private repository naming itself is not a disclosure; a private name inside
# a PUBLIC repository's tracked files is. Until now the disclosure audit ran
# against the control plane alone (`check_portfolio_privacy.sh` passes a single
# --root), so every other public repository was unaudited — which is how tracked
# directories named after private repositories went unnoticed in public repos.
DISCLOSURE_COUNT=0

# The registry is gitignored, so it exists only in the main checkout. Resolving
# it relative to this script would find nothing inside a linked worktree and
# skip the sweep — the same fail-open that let a disclosure through on
# 2026-08-23. Resolve the main checkout explicitly.
CONTROL_PLANE="$(cd "${SCRIPT_DIR}/.." && pwd)"
cp_common_dir="$(git -C "${CONTROL_PLANE}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "${cp_common_dir}" ]]; then
    cp_main="$(dirname "${cp_common_dir}")"
    if [[ "${cp_main}" != "${CONTROL_PLANE}" && -d "${cp_main}/config/repository-visibility" ]]; then
        CONTROL_PLANE="${cp_main}"
    fi
fi
VIS_DIR="${CONTROL_PLANE}/config/repository-visibility"
PRIVATE_REGISTRY="${TRACTION_CONTROL_PRIVATE_REPOS_CONFIG:-${VIS_DIR}/private.local.json}"
PUBLIC_REGISTRY="${TRACTION_CONTROL_PUBLIC_REPOS_CONFIG:-${VIS_DIR}/public.local.json}"
LOCAL_PRIVATE_REGISTRY="${TRACTION_CONTROL_LOCAL_PRIVATE_REPOS_CONFIG:-${VIS_DIR}/local-private.local.json}"
COMMON_WORDS_LIST="${TRACTION_CONTROL_COMMON_WORDS_CONFIG:-${VIS_DIR}/common-words.local.json}"
[[ -f "${COMMON_WORDS_LIST}" ]] || COMMON_WORDS_LIST=""

if [[ -f "${PRIVATE_REGISTRY}" && -f "${PUBLIC_REGISTRY}" ]]; then
    log "=== private-name disclosure sweep (local Git indexes of registry-public repos) ==="
    log "this sweep does not establish what the hosted default branch contains"
    # Under Bash 3 with nounset, expanding an empty array directly in a for
    # list raises an unbound-variable error. The conditional expansion keeps an
    # empty portfolio a valid no-op audit rather than a failed run.
    for repo in "${REPO_DIRS[@]+"${REPO_DIRS[@]}"}"; do
        rel="${repo#${PORTFOLIO_ROOT}/}"
        slug_url="$(git -C "${repo}" remote get-url origin 2>/dev/null || true)"
        [[ -n "${slug_url}" ]] || continue
        slug="${slug_url##*github.com[:/]}"
        slug="${slug%.git}"
        # Only audit repositories the public registry vouches for. Unregistered
        # or private repositories are skipped: fail-closed means we do not treat
        # unknown visibility as public and start reporting on it.
        grep -q "\"${slug}\"" "${PUBLIC_REGISTRY}" 2>/dev/null || continue

        set +e
        disclosure_output="$(
            PYTHONDONTWRITEBYTECODE=1 python3 "${SCRIPT_DIR}/repository_visibility.py" \
                audit-private-disclosures \
                --private "${PRIVATE_REGISTRY}" \
                --public "${PUBLIC_REGISTRY}" \
                ${LOCAL_PRIVATE_REGISTRY:+--local-private "${LOCAL_PRIVATE_REGISTRY}"} \
                ${COMMON_WORDS_LIST:+--common-words "${COMMON_WORDS_LIST}"} \
                --root "${repo}" 2>&1
        )"
        disclosure_status=$?
        set -e

        if (( disclosure_status != 0 )); then
            hits="$(grep -c '^error:' <<< "${disclosure_output}" || true)"
            warn "${rel}: ${hits} indexed file(s) name a private repository in this local checkout"
            while IFS= read -r line; do
                [[ "${line}" == error:* ]] && warn "  ${line#error: }"
            done <<< "${disclosure_output}"
            DISCLOSURE_COUNT=$(( DISCLOSURE_COUNT + hits ))
        fi
    done
    if (( DISCLOSURE_COUNT == 0 )); then
        log "no private repository names in the scanned local Git indexes"
    fi
else
    warn "disclosure sweep SKIPPED: visibility registry not found at ${PRIVATE_REGISTRY}"
    warn "  this run proves nothing about private-name disclosure in public repos"
fi

# ── local-model serving policy ───────────────────────────────────────────────
# Which engine serves each local model is a reviewed, evidence-backed decision
# kept in crew-chief's config/serving-policy.toml. A model pulled without an
# entry, a cited benchmark that is missing, or a reviewed entry without
# evidence is a governance gap like a missing baseline file.
log ""
log "=== serving policy ==="
POLICY_COUNT=0
CREW_CHIEF_REPO=""
for repo in "${REPO_DIRS[@]}"; do
    case "$(git -C "${repo}" remote get-url origin 2>/dev/null || true)" in
        */crew-chief|*/crew-chief.git) CREW_CHIEF_REPO="${repo}"; break ;;
    esac
done
if [[ -z "${CREW_CHIEF_REPO}" ]]; then
    log "serving policy SKIPPED: no crew-chief checkout found"
elif [[ ! -f "${CREW_CHIEF_REPO}/config/serving-policy.toml" ]]; then
    log "serving policy SKIPPED: ${CREW_CHIEF_REPO#${PORTFOLIO_ROOT}/} has no config/serving-policy.toml on its checked-out branch"
else
    set +e
    policy_output="$(
        PYTHONPATH="${CREW_CHIEF_REPO}/src" \
            CREW_CHIEF_SERVING_POLICY="${CREW_CHIEF_REPO}/config/serving-policy.toml" \
            python3 -m crew_chief policy check --live 2>&1
    )"
    policy_status=$?
    set -e
    case "${policy_status}" in
        0)
            while IFS= read -r line; do
                [[ "${line}" == warning:* ]] && warn "serving policy: ${line#warning: }"
            done <<< "${policy_output}"
            log "serving policy matches the pulled models"
            ;;
        1)
            while IFS= read -r line; do
                case "${line}" in
                    error:*) warn "serving policy: ${line#error: }"; POLICY_COUNT=$(( POLICY_COUNT + 1 )) ;;
                    warning:*) warn "serving policy: ${line#warning: }" ;;
                esac
            done <<< "${policy_output}"
            ;;
        *)
            # Ollama down or the policy unreadable: report, but do not count it
            # as drift — this run cannot see what is pulled.
            warn "serving policy check could not run: $(tail -1 <<< "${policy_output}")"
            ;;
    esac
fi

# ── KeePass vault access ─────────────────────────────────────────────────────
# A sync service that rewrites a vault under another account leaves it
# unreadable for this login, and every consumer then fails with a misleading
# "not readable". auto-pass names each such vault and the fix.
log ""
log "=== vault access ==="
VAULT_COUNT=0
AUTO_PASS_REPO=""
for repo in "${REPO_DIRS[@]}"; do
    case "$(git -C "${repo}" remote get-url origin 2>/dev/null || true)" in
        */auto-pass|*/auto-pass.git) AUTO_PASS_REPO="${repo}"; break ;;
    esac
done
if [[ -z "${AUTO_PASS_REPO}" ]]; then
    log "vault access SKIPPED: no auto-pass checkout found"
elif [[ ! -f "${AUTO_PASS_REPO}/config/auto-pass.env.local" ]]; then
    log "vault access SKIPPED: ${AUTO_PASS_REPO#${PORTFOLIO_ROOT}/} has no config/auto-pass.env.local"
else
    set +e
    vault_output="$(
        cd "${AUTO_PASS_REPO}" && PYTHONPATH="${AUTO_PASS_REPO}/src" \
            python3 -m auto_pass --env-file config/auto-pass.env.local check-access 2>&1
    )"
    vault_status=$?
    set -e
    case "${vault_status}" in
        0) log "${vault_output#ok: }" ;;
        1)
            while IFS= read -r line; do
                [[ "${line}" == error:* ]] || continue
                warn "vault access: ${line#error: }"
                VAULT_COUNT=$(( VAULT_COUNT + 1 ))
            done <<< "${vault_output}"
            ;;
        *)
            # An older auto-pass without the command, or a broken setup: say so
            # without counting it as drift, since this run cannot see the vaults.
            warn "vault access check could not run: $(tail -1 <<< "${vault_output}")"
            ;;
    esac
fi

log ""
log "=== done ==="
log "repos scanned  : ${#REPO_DIRS[@]}"
log "total gaps     : ${GAP_COUNT}"
log "disclosures    : ${DISCLOSURE_COUNT}"
log "serving policy : ${POLICY_COUNT}"
log "vault access   : ${VAULT_COUNT}"
log ""

# keep a stable symlink to the most recent log
ln -sf "${LOG_FILE}" "${LATEST_LINK}"

if (( DISCLOSURE_COUNT > 0 )); then
    log "ACTION REQUIRED: ${DISCLOSURE_COUNT} indexed file(s) in local checkouts of registry-public repositories name a private repository; check the branch before inferring hosted exposure"
fi
if (( GAP_COUNT > 0 || DISCLOSURE_COUNT > 0 || POLICY_COUNT > 0 || VAULT_COUNT > 0 )); then
    log "ACTION REQUIRED: open ${LATEST_LINK} and apply fixes"
    exit 1
fi
exit 0
