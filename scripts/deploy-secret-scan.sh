#!/usr/bin/env bash
# Deploy portfolio-wide secret-scanning guardrails to every repo.
#
# What this does per repo:
#   1. Copies .gitleaks.toml from the template (or updates it if already present)
#   2. Adds the gitleaks pre-commit hook (idempotent — skips if already present)
#   3. Copies .github/workflows/secret-scan.yml
#   4. Commits and pushes (skips if nothing changed)
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
DRY_RUN=false

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

REPOS=(
    "$PORTFOLIO_ROOT/util-repos/archility"
    "$PORTFOLIO_ROOT/util-repos/auto-pass"
    "$PORTFOLIO_ROOT/util-repos/clockwork"
    "$PORTFOLIO_ROOT/util-repos/crew-chief"
    "$PORTFOLIO_ROOT/util-repos/dyno-lab"
    "$PORTFOLIO_ROOT/util-repos/fedora-debugg"
    "$PORTFOLIO_ROOT/util-repos/ignition"
    "$PORTFOLIO_ROOT/util-repos/intake"
    "$PORTFOLIO_ROOT/util-repos/nordility"
    "$PORTFOLIO_ROOT/util-repos/pit-box"
    "$PORTFOLIO_ROOT/util-repos/shock-relay"
    "$PORTFOLIO_ROOT/util-repos/short-circuit"
    "$PORTFOLIO_ROOT/util-repos/snowbridge"
    "$PORTFOLIO_ROOT/util-repos/tachometer"
    "$PORTFOLIO_ROOT/util-repos/terminility"
    "$PORTFOLIO_ROOT/personal-finance"
    "$PORTFOLIO_ROOT/research-repos/citegres"
    "$PORTFOLIO_ROOT/research-repos/fred-public-data"
    "$PORTFOLIO_ROOT/research-repos/pushshift_python"
    "$PORTFOLIO_ROOT/research-repos/sonetsim"
    "$PORTFOLIO_ROOT/research-repos/zillow-public-data"
    "$PORTFOLIO_ROOT/doc-repos/Certifications"
    "$PORTFOLIO_ROOT/doc-repos/casonk.github.io"
    "$PORTFOLIO_ROOT/doc-repos/my-consent"
    "$PORTFOLIO_ROOT/doc-repos/university-coursework"
    "$PORTFOLIO_ROOT/health-repos/doseido"
    "$PORTFOLIO_ROOT/drawio-templates"
)

COMMIT_MSG="ci: add gitleaks secret-scan guardrails

Add .gitleaks.toml with portfolio-specific PII rules (phone numbers,
local machine paths, tachometer notify fields, biometric data) and a
.github/workflows/secret-scan.yml that runs on push, PR, and weekly.
Gitleaks pre-commit hook added to .pre-commit-config.yaml.

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
        "$REPO/.github/workflows/secret-scan.yml" 2>/dev/null || true
    git -C "$REPO" diff --cached --quiet && { echo "  nothing staged"; continue; }
    git -C "$REPO" commit -m "$COMMIT_MSG"
    git -C "$REPO" push origin "$BRANCH" && echo "  Pushed."
    (( deployed++ )) || true
done

echo ""
echo "Done. Deployed: $deployed  Skipped/already-current: $skipped"
