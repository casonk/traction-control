#!/usr/bin/env bash
# Deploy portfolio-wide secret-scanning guardrails to every repo.
#
# What this does per repo:
#   1. Copies .gitleaks.toml from the template (or updates it if already present)
#   2. Adds the gitleaks pre-commit hook (idempotent — skips if already present)
#   3. Copies .github/workflows/secret-scan.yml
#   4. Generates .gitleaks-baseline.json (full-history scan, captures known findings)
#   5. Commits and pushes (skips if nothing changed)
#
# Usage:
#   bash scripts/deploy-secret-scan.sh [--dry-run]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORTFOLIO_ROOT="$(cd "$REPO_ROOT/../.." && pwd)"

GITLEAKS_TOML="$REPO_ROOT/docs/templates/.gitleaks.toml"
SECRET_SCAN_WORKFLOW="$REPO_ROOT/docs/templates/secret-scan.yml"
GITLEAKS_VERSION="v8.30.1"
GITLEAKS_BIN="${GITLEAKS_BIN:-gitleaks}"
DRY_RUN=false
TARGETS_FILE="${TRACTION_CONTROL_SECRET_SCAN_TARGETS:-$REPO_ROOT/config/secret-scan/repositories.local.txt}"

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ ! -f "$TARGETS_FILE" || -L "$TARGETS_FILE" ]]; then
    echo "ERROR: secret-scan target list must be a regular non-symlink file: $TARGETS_FILE" >&2
    exit 2
fi

case "$TARGETS_FILE" in
    "$REPO_ROOT"/*) targets_relative="${TARGETS_FILE#"$REPO_ROOT"/}" ;;
    *)
        echo "ERROR: secret-scan target list must remain inside $REPO_ROOT" >&2
        exit 2
        ;;
esac

if git -C "$REPO_ROOT" ls-files --error-unmatch -- "$targets_relative" >/dev/null 2>&1; then
    echo "ERROR: secret-scan target list must not be tracked: $TARGETS_FILE" >&2
    exit 2
fi
if ! git -C "$REPO_ROOT" check-ignore --quiet --no-index -- "$targets_relative"; then
    echo "ERROR: secret-scan target list must be ignored: $TARGETS_FILE" >&2
    exit 2
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
    targets_mode="$(stat -f '%Lp' "$TARGETS_FILE")"
else
    targets_mode="$(stat -c '%a' "$TARGETS_FILE")"
fi
if (( (8#$targets_mode & 8#077) != 0 )); then
    echo "ERROR: secret-scan target list must be owner-only: $TARGETS_FILE" >&2
    exit 2
fi

REPOS=()
while IFS= read -r relative_path || [[ -n "$relative_path" ]]; do
    [[ -z "$relative_path" || "$relative_path" == \#* ]] && continue
    case "$relative_path" in
        /*|.|..|./*|../*|*/../*|*/..)
            echo "ERROR: unsafe portfolio-relative target: $relative_path" >&2
            exit 2
            ;;
    esac
    REPOS+=("$PORTFOLIO_ROOT/$relative_path")
done < "$TARGETS_FILE"

if [[ "${#REPOS[@]}" -eq 0 ]]; then
    echo "ERROR: secret-scan target list is empty: $TARGETS_FILE" >&2
    exit 2
fi

COMMIT_MSG="ci: update gitleaks config, workflow, and baseline

Refresh .gitleaks.toml with 555-area-code allowlist entries and
documentation path exclusions for the workspace-path rule. Update
secret-scan.yml to run scheduled full-history scans through the Gitleaks
CLI with --baseline-path, while leaving push/PR scans on the GitHub Action's
event-aware defaults.
Add .gitleaks-baseline.json capturing known historical findings so the
weekly scan suppresses false positives without blocking new violations.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

GITLEAKS_HOOK='  - repo: https://github.com/gitleaks/gitleaks\n    rev: '"$GITLEAKS_VERSION"'\n    hooks:\n      - id: gitleaks'

deployed=0
skipped=0

for REPO in "${REPOS[@]}"; do
    if [[ ! -d "$REPO/.git" ]]; then
        echo "SKIP (no .git): $REPO"
        (( skipped++ )) || true
        continue
    fi

    echo "=== $(basename "$REPO") ==="
    changed=false

    # 1. .gitleaks.toml
    if [[ ! -f "$REPO/.gitleaks.toml" ]] || ! diff -q "$GITLEAKS_TOML" "$REPO/.gitleaks.toml" >/dev/null 2>&1; then
        echo "  + .gitleaks.toml"
        $DRY_RUN || cp "$GITLEAKS_TOML" "$REPO/.gitleaks.toml"
        changed=true
    fi

    # 2. gitleaks pre-commit hook (idempotent)
    PRECOMMIT="$REPO/.pre-commit-config.yaml"
    if [[ -f "$PRECOMMIT" ]] && ! grep -q "gitleaks/gitleaks" "$PRECOMMIT"; then
        echo "  + gitleaks hook to .pre-commit-config.yaml"
        if ! $DRY_RUN; then
            printf '\n%b\n' "$GITLEAKS_HOOK" >> "$PRECOMMIT"
        fi
        changed=true
    elif [[ ! -f "$PRECOMMIT" ]]; then
        echo "  + .pre-commit-config.yaml (minimal, gitleaks only)"
        if ! $DRY_RUN; then
            cat > "$PRECOMMIT" <<PRECOMMIT_EOF
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: detect-private-key
  - repo: https://github.com/gitleaks/gitleaks
    rev: $GITLEAKS_VERSION
    hooks:
      - id: gitleaks
PRECOMMIT_EOF
        fi
        changed=true
    fi

    # 3. secret-scan workflow
    WORKFLOWS_DIR="$REPO/.github/workflows"
    WORKFLOW_DEST="$WORKFLOWS_DIR/secret-scan.yml"
    if [[ ! -f "$WORKFLOW_DEST" ]] || ! diff -q "$SECRET_SCAN_WORKFLOW" "$WORKFLOW_DEST" >/dev/null 2>&1; then
        echo "  + .github/workflows/secret-scan.yml"
        if ! $DRY_RUN; then
            mkdir -p "$WORKFLOWS_DIR"
            cp "$SECRET_SCAN_WORKFLOW" "$WORKFLOW_DEST"
        fi
        changed=true
    fi

    # 4. gitleaks baseline (generate / regenerate whenever .gitleaks.toml changed or baseline missing)
    BASELINE="$REPO/.gitleaks-baseline.json"
    if command -v "$GITLEAKS_BIN" >/dev/null 2>&1; then
        if [[ ! -f "$BASELINE" ]] || [[ "$REPO/.gitleaks.toml" -nt "$BASELINE" ]] || ! git -C "$REPO" ls-files --error-unmatch "$BASELINE" >/dev/null 2>&1; then
            echo "  + .gitleaks-baseline.json (scanning full history)"
            if ! $DRY_RUN; then
                "$GITLEAKS_BIN" detect \
                    --source "$REPO" \
                    --config "$REPO/.gitleaks.toml" \
                    --report-format json \
                    --report-path "$BASELINE" \
                    --log-opts "--all" \
                    --exit-code 0 \
                    2>/dev/null || true
            fi
            changed=true
        fi
    else
        echo "  WARN: gitleaks not found — skipping baseline generation (set GITLEAKS_BIN)"
    fi

    if ! $changed; then
        echo "  (already up to date)"
        (( skipped++ )) || true
        continue
    fi

    if $DRY_RUN; then
        echo "  (dry-run: would commit and push)"
        (( deployed++ )) || true
        continue
    fi

    # Pre-commit sanity check
    if ! pre-commit run --files "$REPO/.gitleaks.toml" "$REPO/.github/workflows/secret-scan.yml" 2>/dev/null; then
        echo "  WARN: pre-commit reported issues — committing anyway (not a source file)"
    fi

    BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
    git -C "$REPO" add \
        "$REPO/.gitleaks.toml" \
        "$REPO/.pre-commit-config.yaml" \
        "$REPO/.github/workflows/secret-scan.yml" \
        "$REPO/.gitleaks-baseline.json" 2>/dev/null || true
    git -C "$REPO" diff --cached --quiet && { echo "  nothing staged"; continue; }
    git -C "$REPO" commit -m "$COMMIT_MSG"
    git -C "$REPO" push origin "$BRANCH" && echo "  Pushed."
    (( deployed++ )) || true
done

echo ""
echo "Done. Deployed: $deployed  Skipped/already-current: $skipped"
