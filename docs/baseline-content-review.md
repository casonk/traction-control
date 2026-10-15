# Baseline Content Review

Review the portfolio baseline by what each file says and what the repository
actually does. A file's last commit being the original scaffold is a useful
queueing signal, but a stable license, conduct policy, or editor setting may
be correct for years. Conversely, a recently edited file can still be wrong.

## Review Sequence

1. Discover checkouts from the portfolio root (`../..`), excluding
   `archive-repos/`, and require `.git/HEAD`. Use the ignored visibility
   registry to distinguish public from private repositories. Keep private
   names, paths, and per-repository findings in ignored owner-only reports.
2. Run `scripts/portfolio-audit.sh` for missing files, the current agent and
   security conventions, tracked-file quality, and public disclosure checks.
   Account-level `.github` defaults satisfy contribution, conduct, and GitHub
   template coverage when there is no local override.
3. Compare each file with the repository's current README, manifests, CI,
   release tags, and deployment or data boundaries. Read the latest path
   commit as context; do not use age or a scaffold-like subject as a failure.
4. Record a concrete mismatch and its evidence before changing a file. Make
   repo-specific fixes in that repo, after reading its `AGENTS.md`,
   `LESSONSLEARNED.md`, `BACKLOG.md`, and `CHATHISTORY.md`. Put reusable rules
   and common templates in the control plane.

## File-Specific Questions

| File | Check against current behavior |
|---|---|
| `CONTRIBUTING.md` | Actual setup, tests, PR route, and required disclosure section; account default applies only when there is no local override. Check the repo's `AGENTS.md` for contradictory contribution rules. |
| `SECURITY.md` | Private reporting route, real credentials/data/network surfaces, and safe handling of generated output. |
| `CHANGELOG.md` | Released versions and dates versus tags and package metadata; distinguish an unreleased work log from release notes. |
| `CODE_OF_CONDUCT.md` | Whether a local override is intentional and gives a usable reporting or moderation path; otherwise rely on the account default. |
| `REFS-PUBLIC.md` | Links and dependency descriptions match current tracked integrations; an explicit “none” is valid. Keep local/private references in `REFS-LOCAL.md`. |
| `LICENSE` | License identifier and copyright holder match package metadata and intended distribution; review any change to license terms separately. |
| `.editorconfig` | Rules fit languages and generated files actually in the repo; absence of a YAML section is not automatically a defect. |
| `.gitignore` | Local session/reference files and real generated outputs are ignored, while necessary tracked fixtures and examples remain visible. |

Do not auto-rewrite a license, fabricate changelog releases, duplicate account
defaults into every repo, or add links to `REFS-PUBLIC.md` solely to make a
content metric pass. Tests should flag objective contradictions; subjective
freshness decisions belong in review.

## 2026-10-15 Snapshot

The portfolio-root scan found 47 checkouts. Of the eight file types above,
`SECURITY.md` was last touched by a scaffold/baseline-style commit in 34
checkouts, `CODE_OF_CONDUCT.md` in 39, `LICENSE` in 33, and `.editorconfig`
in 30. These are review candidates, not 136 confirmed defects. The content
scan found 47 local security files, 44 conduct files, and 44 contribution
files. Three checkouts inherit the account defaults for contribution and
conduct. It found 44 `REFS-PUBLIC.md` files; an explicit statement that no
standing references exist is valid.

The existing structural audit found 23 gaps in nine checkouts before the
`REFS-PUBLIC.md` requirement was added: seven
contribution-disclosure gaps, eight agent-convention gaps, seven security
policy gaps, and one missing `.editorconfig`. The release review also found
that the public `sonetsim` and `snowbridge` checkouts have tags but no
versioned sections in `CHANGELOG.md`. This is a release-history review item,
not a reason to invent entries. The exact checkout findings should remain
in the ignored audit log, not in tracked portfolio documentation.
The new requirement will report three additional missing local
`REFS-PUBLIC.md` files on the next run.

The audit also found 12 private-name matches in the local Git index of the
public `intake` checkout. That checkout was on an older `legacy/*` branch;
`main` and `origin/main` had no matching files in the same local check. The
finding calls for branch review before that index is published. It does not
establish a disclosure on the hosted default branch. The audit output now
labels the local-index boundary explicitly.
