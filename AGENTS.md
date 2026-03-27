# AGENTS.md — Portfolio Control Plane

> Scope: this file governs portfolio-wide agent behavior across every git repository under `/mnt/4tb-m2/git/`.
> This file lives in `/mnt/4tb-m2/git/util-repos/traction-control`, not at the portfolio root.

## Working Coordinates

- Control-plane repo: `/mnt/4tb-m2/git/util-repos/traction-control`
- Portfolio root to scan: `/mnt/4tb-m2/git`
- Relative path from this repo to the portfolio root: `../..`

When these instructions refer to:

- `portfolio root`: `/mnt/4tb-m2/git`
- `repo paths`: paths relative to `/mnt/4tb-m2/git`, not relative to `traction-control`

Before auditing or inventorying repositories, scan from the portfolio root.

Example:

```bash
PORTFOLIO_ROOT="$(cd ../.. && pwd)"
find "$PORTFOLIO_ROOT" -maxdepth 4 -type d -name .git | sort
```

Repo-level `AGENTS.md` files override this document for repo-specific behavior.

## Repository Landscape

| Repository | Path From Portfolio Root | Type | Notes |
|---|---|---|---|
| `casonk.github.io` | `./doc-repos/casonk.github.io` | Jekyll / Ruby | Personal portfolio website |
| `Certifications` | `./doc-repos/Certifications` | Docs / Markdown | Certification and recognition archive |
| `university-coursework` | `./doc-repos/university-coursework` | Mixed archive | Coursework repository spanning multiple disciplines |
| `drawio-templates` | `./drawio-templates` | Templates | Reusable draw.io diagrams |
| `personal-finance` | `./personal-finance` | Python | Personal finance ingestion, normalization, and reporting |
| `citegres` | `./research-repos/citegres` | Python / tkinter | PostgreSQL GUI academic project |
| `pushshift_python` | `./research-repos/pushshift_python` | Python | Reddit analytics and research tooling |
| `sonetsim` | `./research-repos/sonetsim` | Python package | Social network simulation library |
| `zillow-public-data` | `./research-repos/zillow-public-data` | Python | Zillow dataset mirror and visualization tooling |
| `auto-pass` | `./util-repos/auto-pass` | Python package | KeePassXC-backed password automation helpers |
| `fedora-debugg` | `./util-repos/fedora-debugg` | Bash / Shell | Fedora workstation crash triage toolkit |
| `nordility` | `./util-repos/nordility` | Python package | NordVPN CLI/API automation |
| `shock-relay` | `./util-repos/shock-relay` | Python / Shell | Cross-platform messaging relay tooling |
| `terminility` | `./util-repos/terminility` | Bash / Shell | tmux installation and session management |
| `traction-control` | `./util-repos/traction-control` | Governance / Docs | Portfolio-wide agent control-plane repo |

Non-repo folder:

- `archive-repos/` contains archive artifacts only.

## Portfolio Baseline

Current strong baseline across the portfolio:

- every repo should have `README.md`, `LICENSE`, `.gitignore`, `AGENTS.md`, `CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and issue templates
- repo-root `CHATHISTORY.md` is the standard local handoff file everywhere
- `.editorconfig` and pre-commit coverage are improved but still not universal
- `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `CHANGELOG.md` are still uneven outside this control-plane repo

Re-scan before making claims based on exact counts. This layer should stay accurate without becoming stale.

## Local Session Memory Standard

- Each repository should use repo-root `CHATHISTORY.md` as the standard local handoff log.
- `CHATHISTORY.md` is local-only, gitignored, and must not be committed.
- Read repo `CHATHISTORY.md` after repo `AGENTS.md` when resuming work in a specific repository.
- Portfolio-wide cross-repo work uses `traction-control/CHATHISTORY.md`.

## Current Portfolio Priorities

### P0 — Governance

- Add `SECURITY.md` across public repositories.
- Add `CODE_OF_CONDUCT.md` across public repositories.

### P1 — Release Hygiene

- Introduce `CHANGELOG.md` where releases or user-facing versions matter.
- Keep tags and version metadata aligned in packaged projects.

### P2 — Tooling Consistency

- Add `.editorconfig` to repos still missing it.
- Expand pre-commit coverage in repos that already have tests or CI.

### P3 — CI And Verification

- Continue standardizing lightweight CI and smoke tests in newer code repos.
- Prefer repo-appropriate checks over one-size-fits-all workflows.

## Baseline For New Repositories

### Tier 1 — Mandatory

Every new repository should start with:

- `README.md`
- `LICENSE`
- `.gitignore`
- `AGENTS.md`
- `.editorconfig`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/ISSUE_TEMPLATE/feature_request.md`

`README.md` should include:

- project name and one-line description
- prerequisites or install steps
- usage or quick start
- contributing reference
- license reference

License default guidance:

- code projects: `MIT`
- research or academic code: `LGPL-2.1` or `Apache-2.0`
- documentation-only repositories: `CC-BY-4.0`
- private personal-data repositories: keep private, no default public license

### Tier 2 — Required For Code Repositories

Add:

- `tests/`
- `.github/workflows/ci.yml`
- `.pre-commit-config.yaml`
- `docs/contributor-architecture-blueprint.md`

For Python repos:

- use `pyproject.toml` as the primary package metadata source
- keep `requirements.txt` only when it materially helps reproducibility or CI

### Tier 3 — Recommended For Mature Repositories

Add:

- `CHANGELOG.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`

## Best Current Internal References

- `./util-repos/nordility`: strongest repo-level `AGENTS.md`
- `./personal-finance`: strongest CI, test depth, and contributor workflow baseline
- `./research-repos/sonetsim`: strongest packaging and release alignment
- `./doc-repos/Certifications` and `./doc-repos/university-coursework`: strong examples of documentation-first repository organization

## Agent Operating Rules

1. For cross-repo work, read `traction-control/AGENTS.md` and `traction-control/CHATHISTORY.md` first.
2. Then scan from the portfolio root `/mnt/4tb-m2/git`, not from `/mnt/4tb-m2/git/util-repos/traction-control`.
3. Read the target repo’s `AGENTS.md` and `CHATHISTORY.md` before making repo-specific changes.
4. Never commit secrets, credentials, API keys, personal financial data, or local-only config files.
5. Do not modify files outside the repository you are explicitly working in unless the user asks for cross-repo work.
6. Run relevant verification before and after substantive changes when feasible. For docs-only changes, targeted validation is acceptable.
7. Use Conventional Commits for any git operations: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`.
8. Prefer additive, PR-ready changes. Do not rewrite history or remove user data unless explicitly instructed.
9. Preserve established architecture, naming, and folder conventions unless the task explicitly calls for restructuring.
10. When a repo contains architecture docs, diagrams, or workflow docs, keep them in sync with behavioral changes.

Last reviewed: `2026-03-26`
