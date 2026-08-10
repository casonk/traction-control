#!/usr/bin/env bash
# Offline contract test for the private-repository bundle overlay renderer.
#
# Uses synthetic registries and bundles only. It never touches GitHub, the real
# local registries, or the operator's rendered bundle.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RENDERER="${REPO_ROOT}/scripts/render_private_bundle_overlay.sh"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

PASS=0
FAIL=0

ok() { printf 'ok: %s\n' "$*"; PASS=$((PASS + 1)); }
bad() { printf 'FAIL: %s\n' "$*" >&2; FAIL=$((FAIL + 1)); }

BASE="${WORK_DIR}/repos.conf"
PRIVATE_REGISTRY="${WORK_DIR}/private.json"
PUBLIC_REGISTRY="${WORK_DIR}/public.json"

cat > "${BASE}" <<'EOF'
# profiles|name|github_slug|relative_path|purpose
light,moderate,heavy|traction-control|example-owner/example-public-primary|util-repos/traction-control|Base entry
moderate,heavy|example-public-tool|example-owner/example-public-secondary|util-repos/example-public-tool|Base entry
EOF

cat > "${PRIVATE_REGISTRY}" <<'EOF'
{
  "schema_version": 1,
  "registry_id": "test-portfolio",
  "generation": 1,
  "visibility": "private",
  "repositories": [
    {"id": "R_TEST_PRIVATE_001", "slug": "example-owner/example-private-repository"},
    {"id": "R_TEST_PRIVATE_002", "slug": "example-owner/example-private-secondary"}
  ]
}
EOF

cat > "${PUBLIC_REGISTRY}" <<'EOF'
{
  "schema_version": 1,
  "registry_id": "test-portfolio",
  "generation": 1,
  "visibility": "public",
  "repositories": [
    {"id": "R_TEST_PUBLIC_001", "slug": "example-owner/example-public-primary"},
    {"id": "R_TEST_PUBLIC_002", "slug": "example-owner/example-public-secondary"}
  ]
}
EOF

# The visibility registry refuses group- or world-readable registry files.
chmod 600 "${PRIVATE_REGISTRY}" "${PUBLIC_REGISTRY}"

run_renderer() {
  local overlay="$1"
  local output="$2"
  shift 2
  TRACTION_CONTROL_PRIVATE_REPOS_CONFIG="${PRIVATE_REGISTRY}" \
  TRACTION_CONTROL_PUBLIC_REPOS_CONFIG="${PUBLIC_REGISTRY}" \
    bash "${RENDERER}" \
      --base-config "${BASE}" \
      --private-config "${overlay}" \
      --output "${output}" \
      "$@"
}

expect_failure() {
  local label="$1"
  local overlay="$2"
  local output="${WORK_DIR}/out-reject.conf"
  rm -f "${output}"
  if run_renderer "${overlay}" "${output}" >/dev/null 2>&1; then
    bad "${label}: renderer accepted input it must refuse"
  elif [[ -e "${output}" ]]; then
    bad "${label}: renderer wrote output while failing"
  else
    ok "${label}"
  fi
}

# 1. Valid private overlay renders.
VALID="${WORK_DIR}/valid.conf"
cat > "${VALID}" <<'EOF'
# comment line is ignored
light,moderate,heavy|example-private-docs|example-owner/example-private-repository|util-repos/example-private-docs|Private docs
heavy|example-private-secrets|example-owner/example-private-secondary|util-repos/example-private-secrets|Private secrets
EOF

OUT="${WORK_DIR}/out.conf"
if run_renderer "${VALID}" "${OUT}" >/dev/null 2>&1; then
  ok "valid private overlay renders"
else
  bad "valid private overlay was refused"
fi

if grep -q "example-owner/example-public-primary" "${OUT}" 2>/dev/null &&
   grep -q "example-owner/example-private-repository" "${OUT}" 2>/dev/null; then
  ok "merged bundle keeps tracked base entries and adds private entries"
else
  bad "merged bundle is missing base or private entries"
fi

MODE="$(stat -f '%Lp' "${OUT}" 2>/dev/null || stat -c '%a' "${OUT}" 2>/dev/null || echo missing)"
if [[ "${MODE}" == "600" ]]; then
  ok "rendered bundle is owner-only (${MODE})"
else
  bad "rendered bundle mode is ${MODE}, expected 600"
fi

# 2. Public slug in the private overlay is refused.
PUBLIC_SLUG="${WORK_DIR}/public-slug.conf"
cat > "${PUBLIC_SLUG}" <<'EOF'
light|example-public-tool-again|example-owner/example-public-secondary|util-repos/x|Public slug
EOF
expect_failure "public slug in private overlay is refused" "${PUBLIC_SLUG}"

# 3. Unclassified slug is refused.
UNCLASSIFIED="${WORK_DIR}/unclassified.conf"
cat > "${UNCLASSIFIED}" <<'EOF'
light|mystery|example-owner/example-unrecorded|util-repos/mystery|Not in either registry
EOF
expect_failure "unclassified slug is refused" "${UNCLASSIFIED}"

# 4. Invalid profile name is refused.
BAD_PROFILE="${WORK_DIR}/bad-profile.conf"
cat > "${BAD_PROFILE}" <<'EOF'
extreme|example-private-docs|example-owner/example-private-repository|util-repos/x|Bad profile
EOF
expect_failure "invalid profile name is refused" "${BAD_PROFILE}"

# 5. Malformed field count is refused.
MALFORMED="${WORK_DIR}/malformed.conf"
cat > "${MALFORMED}" <<'EOF'
light|example-private-docs|example-owner/example-private-repository|util-repos/x|Purpose|extra
EOF
expect_failure "extra pipe field is refused" "${MALFORMED}"

# 6. Name colliding with the tracked base is refused.
COLLISION="${WORK_DIR}/collision.conf"
cat > "${COLLISION}" <<'EOF'
light|traction-control|example-owner/example-private-repository|util-repos/x|Shadows a base entry
EOF
expect_failure "name collision with the tracked base is refused" "${COLLISION}"

# 7. Duplicate overlay entries are refused.
DUPLICATE="${WORK_DIR}/duplicate.conf"
cat > "${DUPLICATE}" <<'EOF'
light|example-private-docs|example-owner/example-private-repository|util-repos/x|First
heavy|example-private-docs|example-owner/example-private-secondary|util-repos/y|Duplicate name
EOF
expect_failure "duplicate overlay name is refused" "${DUPLICATE}"

# 8. An empty (all-commented) overlay is refused rather than silently rendering the base.
EMPTY="${WORK_DIR}/empty.conf"
cat > "${EMPTY}" <<'EOF'
# nothing enrolled yet
EOF
expect_failure "overlay with no enrolled repositories is refused" "${EMPTY}"

# 9. A Git-tracked overlay is refused.
TRACKED_DIR="${WORK_DIR}/tracked"
mkdir -p "${TRACKED_DIR}"
git -C "${TRACKED_DIR}" init -q
git -C "${TRACKED_DIR}" config user.email test@example.com
git -C "${TRACKED_DIR}" config user.name test
TRACKED_OVERLAY="${TRACKED_DIR}/private-repos.local.conf"
cat > "${TRACKED_OVERLAY}" <<'EOF'
light|example-private-docs|example-owner/example-private-repository|util-repos/x|Tracked overlay
EOF
git -C "${TRACKED_DIR}" add -f private-repos.local.conf
git -C "${TRACKED_DIR}" commit -qm "track overlay"
expect_failure "Git-tracked overlay is refused" "${TRACKED_OVERLAY}"

# 9b. An untracked but unignored overlay inside a work tree is refused.
UNIGNORED_OVERLAY="${TRACKED_DIR}/unignored.local.conf"
cat > "${UNIGNORED_OVERLAY}" <<'EOF'
light|example-private-docs|example-owner/example-private-repository|util-repos/x|Unignored overlay
EOF
expect_failure "unignored overlay inside a work tree is refused" "${UNIGNORED_OVERLAY}"

# 9c. The same overlay is accepted once an ignore rule covers it.
printf 'unignored.local.conf\n' > "${TRACKED_DIR}/.gitignore"
IGNORED_OUT="${WORK_DIR}/out-ignored.conf"
if run_renderer "${UNIGNORED_OVERLAY}" "${IGNORED_OUT}" >/dev/null 2>&1; then
  ok "ignored overlay inside a work tree is accepted"
else
  bad "ignored overlay inside a work tree was refused"
fi

# 10. --dry-run validates without writing.
DRY_OUT="${WORK_DIR}/dry.conf"
if run_renderer "${VALID}" "${DRY_OUT}" --dry-run >/dev/null 2>&1 && [[ ! -e "${DRY_OUT}" ]]; then
  ok "--dry-run validates without writing output"
else
  bad "--dry-run wrote output or failed validation"
fi

# 11. --init refuses to clobber an existing overlay.
if ! run_renderer "${VALID}" "${WORK_DIR}/init.conf" --init >/dev/null 2>&1; then
  ok "--init refuses to overwrite an existing overlay"
else
  bad "--init overwrote an existing overlay"
fi

# 12. --init writes commented-out candidates only.
INIT_OVERLAY="${WORK_DIR}/generated.conf"
if run_renderer "${INIT_OVERLAY}" "${WORK_DIR}/unused.conf" --init >/dev/null 2>&1 &&
   grep -q "^# light,moderate,heavy|example-private-repository|" "${INIT_OVERLAY}" 2>/dev/null &&
   ! grep -qE "^[^#]" "${INIT_OVERLAY}" 2>/dev/null; then
  ok "--init enrolls nothing: every candidate is commented out"
else
  bad "--init did not produce commented-out candidates"
fi

printf '\n%d passed, %d failed\n' "${PASS}" "${FAIL}"
(( FAIL == 0 ))
