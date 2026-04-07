# AGENTS.md — Portfolio Control Plane

> Scope: this file governs portfolio-wide agent behavior across every git repository in the portfolio workspace.
> This file lives in `./util-repos/traction-control`, not at the portfolio root.

## Working Coordinates

- Control-plane repo: `./util-repos/traction-control`
- Portfolio root to scan: `../..`
- Relative path from this repo to the portfolio root: `../..`

When these instructions refer to:

- `portfolio root`: the directory at `../..` from this repo
- `repo paths`: paths relative to the portfolio root, not relative to `traction-control`

Before auditing or inventorying repositories, scan from the portfolio root.

Example:

```bash
PORTFOLIO_ROOT="$(cd ../.. && pwd)"
find "$PORTFOLIO_ROOT" -maxdepth 4 -type d -name .git | sort
```

## GitHub Access Notes

- Before starting a new `gh auth login` flow, check whether GitHub CLI auth is already active for the current user.
- An SSH key is present in the environment, so repo remotes may use SSH when that is the cleaner publishing path.

## Session Continuity And Reporting

- Read `CHATHISTORY.md` for recent local session continuity before resuming portfolio-wide work.
- Read `LESSONSLEARNED.md` for durable operational lessons before repeating setup, publishing, or audit workflows.
- Always update `CHATHISTORY.md` after a meaningful session.
- Always report the relevant prior history you relied on when continuing work for the user.
- When a reusable lesson is discovered during a request, add it to `LESSONSLEARNED.md`.

Repo-level `AGENTS.md` files override this document for repo-specific behavior.

## Repository Landscape

| Repository | Path From Portfolio Root | Type | Notes |
|---|---|---|---|
| `casonk.github.io` | `./doc-repos/casonk.github.io` | Jekyll / Ruby | Personal portfolio website |
| `my-consent` | `./doc-repos/my-consent` | Docs / Markdown | Personal consent and data-processing consent statements |
| `Certifications` | `./doc-repos/Certifications` | Docs / Markdown | Certification and recognition archive |
| `university-coursework` | `./doc-repos/university-coursework` | Mixed archive | Coursework repository spanning multiple disciplines |
| `drawio-templates` | `./drawio-templates` | Templates | Reusable draw.io diagrams |
| `doseido` | `./health-repos/doseido` | Python | Private supplement sourcing and schema-enrichment tooling |
| `personal-finance` | `./personal-finance` | Python | Personal finance ingestion, normalization, and reporting |
| `citegres` | `./research-repos/citegres` | Python / tkinter | PostgreSQL GUI academic project |
| `pushshift_python` | `./research-repos/pushshift_python` | Python | Reddit analytics and research tooling |
| `sonetsim` | `./research-repos/sonetsim` | Python package | Social network simulation library |
| `zillow-public-data` | `./research-repos/zillow-public-data` | Python | Zillow dataset mirror and visualization tooling |
| `archility` | `./util-repos/archility` | Python package | Architecture toolchain bootstrap/render orchestration, Graphviz-capable diagram support, deterministic starter generation, agentic architecture authoring, and drift-check tooling |
| `auto-pass` | `./util-repos/auto-pass` | Python package | KeePassXC-backed password automation helpers |
| `clockwork` | `./util-repos/clockwork` | Python package | Shared cron and systemd scheduler manifest rendering and install guidance |
| `tachometer` | `./util-repos/tachometer` | Python package | Shared repo and resource profiling helpers plus manifest-driven local profile conventions |
| `fedora-debugg` | `./util-repos/fedora-debugg` | Bash / Shell | Fedora workstation crash triage toolkit |
| `nordility` | `./util-repos/nordility` | Python package | NordVPN CLI/API automation |
| `shock-relay` | `./util-repos/shock-relay` | Python / Shell | Cross-platform messaging relay tooling |
| `pit-box` | `./util-repos/pit-box` | Bash / Shell | WireGuard + SSH hardened remote-access scaffold with settings-driven config rendering |
| `short-circuit` | `./util-repos/short-circuit` | Bash / Shell | WireGuard VPN setup and configuration utility |
| `snowbridge` | `./util-repos/snowbridge` | SMB / Ops | SMB-based private file-sharing and phone-access utility repo |
| `intake` | `./util-repos/intake` | Python | Receipt PDF ingestion, categorization, SQLite storage, and Markdown/HTML reporting from snowbridge share |
| `terminility` | `./util-repos/terminility` | Bash / Shell | tmux installation and session management |
| `dyno-lab` | `./util-repos/dyno-lab` | Python package | Portfolio-wide test bench utilities (fixtures, mocks, assertions, smoke scaffolding) |
| `crew-chief` | `./util-repos/crew-chief` | Python package / Container | Local Ollama LLM service (Podman) and zero-dependency Python client for portfolio-wide trivial inference tasks |
| `traction-control` | `./util-repos/traction-control` | Governance / Docs | Portfolio-wide agent control-plane repo |

Non-repo folder:

- `archive-repos/` contains archive artifacts only.

## Shared Utility Repos

These utility repositories are the portfolio-standard implementation homes for common operational capabilities:

- `./util-repos/archility`: architecture toolchain bootstrap/render orchestration, Graphviz-capable diagram support, deterministic starter-layout generation, agentic architecture authoring, architecture inventory, and architecture-documentation drift checks
- `./util-repos/auto-pass`: password management and KeePassXC-backed secret retrieval/update flows
- `./util-repos/clockwork`: shared cron and systemd scheduler manifest rendering, unit-file generation, and install guidance
- `./util-repos/tachometer`: shared repo and resource profiling, profiled command runs, repo-local manifest loading, and local JSON summary generation
- `./util-repos/nordility`: NordVPN-based VPN switching and connection orchestration
- `./util-repos/shock-relay`: external messaging across supported providers such as Signal, Telegram, Twilio SMS, WhatsApp, and Gmail IMAP
- `./util-repos/short-circuit`: WireGuard VPN setup and configuration utility for establishing private tunnels with SMB, HTTPS, and SSH access
- `./util-repos/snowbridge`: SMB-based private file sharing and phone-accessible fileshare workflows
- `./util-repos/dyno-lab`: unified test bench utilities — fixtures, subprocess/HTTP/env mocks, schema validation, smoke scaffolding, and pytest markers/fixtures
- `./util-repos/crew-chief`: local Ollama LLM service (Podman container) and zero-dependency Python client for trivial inference tasks across portfolio repos

When another repo needs one of these capabilities, prefer integrating with the relevant shared utility repo instead of re-implementing the capability locally.

## Portfolio Baseline

Current strong baseline across the portfolio:

- every repo now has `README.md`, `LICENSE`, `.gitignore`, `AGENTS.md`, `CONTRIBUTING.md`, `LESSONSLEARNED.md`, `.editorconfig`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and issue templates
- every repo now has `REFS-PUBLIC.md` (tracked, public external references) and `REFS-LOCAL.md` (gitignored, machine-specific local paths)
- every repo-level `AGENTS.md` should point back to `./util-repos/traction-control` for portfolio-wide standards and baseline conventions
- every repo-level `AGENTS.md` should also mention the shared utility repos available for architecture toolchain bootstrap/rendering, Graphviz-backed diagram support, deterministic architecture scaffolding, agentic architecture authoring, password management, shared cron and systemd scheduling, repo and resource profiling, VPN switching, WireGuard VPN setup, external messaging, SMB-based file sharing, unified test bench utilities, and local LLM inference
- repo-root `LESSONSLEARNED.md` is the tracked durable-lessons file everywhere
- new repos should seed `LESSONSLEARNED.md` from `./util-repos/traction-control/docs/templates/LESSONSLEARNED.md` instead of leaving only a placeholder entry
- repo-root `REFS-PUBLIC.md` documents public external dependencies; `REFS-LOCAL.md` is gitignored and holds machine-specific paths
- repo-root `CHATHISTORY.md` is the standard local handoff file everywhere
- pre-commit coverage is now portfolio-wide
- every repo should keep the shared architecture starter layout under `docs/`:
  - `docs/contributor-architecture-blueprint.md`
  - `docs/diagrams/repo-architecture.puml`
  - `docs/diagrams/repo-architecture.drawio`
- code-focused repos should still carry lightweight CI and expand the starter blueprint into repo-specific flow detail when the code path is non-trivial

Re-scan before making claims based on exact counts. This layer should stay accurate without becoming stale.

## Local Session Memory Standard

- Each repository should use repo-root `LESSONSLEARNED.md` as the tracked durable-lessons file.
- Each repository should use repo-root `CHATHISTORY.md` as the standard local handoff log.
- Each repository should use repo-root `REFS-PUBLIC.md` to document tracked public external dependencies.
- Each repository should use repo-root `REFS-LOCAL.md` for machine-specific local paths (gitignored).
- `LESSONSLEARNED.md` is tracked and should contain only reusable lessons that should change future sessions.
- `CHATHISTORY.md` is local-only, gitignored, and must not be committed.
- `REFS-LOCAL.md` is local-only, gitignored, and must not be committed.
- `REFS-PUBLIC.md` is tracked and must remain free of private or local-only details.
- Read repo `LESSONSLEARNED.md` and `CHATHISTORY.md` after repo `AGENTS.md` when resuming work in a specific repository.
- Portfolio-wide cross-repo work uses `traction-control/CHATHISTORY.md`.
- Use tracked `LESSONSLEARNED.md` for durable lessons that should survive across local chat-history rotations.

## Current Portfolio Priorities

### P0 — Governance Maintenance

- Keep the baseline governance files in place for every new repository from day one.
- Backfill missing standards immediately when a new public repository is added to the portfolio.

### P1 — Release Hygiene

- Keep tags and version metadata aligned in packaged projects.
- Replace placeholder `CHANGELOG.md` entries with meaningful release notes as repositories mature.

### P2 — Tooling Consistency

- Keep `.editorconfig`, pre-commit, and architecture docs aligned with actual repo behavior.
- Keep the architecture starter filenames and directory layout stable across repos so `archility` automation stays deterministic.
- Avoid stale scaffolding: if workflows, diagrams, or contributor docs stop matching reality, update them in the same change.

### P3 — CI And Verification

- Expand lightweight CI and smoke tests where a repo only has baseline validation today.
- Prefer repo-appropriate checks over one-size-fits-all workflows.

## Baseline For New Repositories

### Tier 1 — Mandatory

Every new repository should start with:

- `README.md`
- `LICENSE`
- `.gitignore`
- `AGENTS.md`
- `LESSONSLEARNED.md` seeded from `./util-repos/traction-control/docs/templates/LESSONSLEARNED.md`
- `.editorconfig`
- `REFS-PUBLIC.md` seeded from `./util-repos/traction-control/docs/templates/REFS-PUBLIC.md`
- `REFS-LOCAL.md` seeded from `./util-repos/traction-control/docs/templates/REFS-LOCAL.md` and added to `.gitignore`
- `docs/contributor-architecture-blueprint.md`
- `docs/diagrams/repo-architecture.puml`
- `docs/diagrams/repo-architecture.drawio`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/ISSUE_TEMPLATE/feature_request.md`

Repo-level `AGENTS.md` files should include a short portfolio standards reference that points to `./util-repos/traction-control`.
Repo-level `AGENTS.md` files should also mention the shared utility repos `./util-repos/archility`, `./util-repos/auto-pass`, `./util-repos/clockwork`, `./util-repos/tachometer`, `./util-repos/nordility`, `./util-repos/shock-relay`, `./util-repos/short-circuit`, `./util-repos/snowbridge`, `./util-repos/dyno-lab`, and `./util-repos/crew-chief` so agents can find the standard architecture bootstrap/render path, Graphviz-backed diagram tooling, deterministic architecture scaffolding, agentic architecture authoring, password-management, shared cron and systemd scheduling, repo and resource profiling, VPN-switching, external-messaging, WireGuard VPN setup, SMB-based file-sharing, unified test bench implementations, and local LLM inference.
New repos should initialize `LESSONSLEARNED.md` from `./util-repos/traction-control/docs/templates/LESSONSLEARNED.md` and keep the shared baseline lessons unless a repo-specific lesson already captures the same operating rule more precisely.

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

For Python repos:

- use `pyproject.toml` as the primary package metadata source
- keep `requirements.txt` only when it materially helps reproducibility or CI

### Tier 3 — Recommended For Mature Repositories

Add:

- `CHANGELOG.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`

## Best Current Internal References

- `./util-repos/archility`: standard architecture bootstrap/render, Graphviz-capable diagram utility, and blueprint-audit utility for other repos
- `./util-repos/archility`: standard deterministic starter-layout generator and agentic architecture-authoring home for other repos
- `./util-repos/auto-pass`: standard password-management utility for other repos
- `./util-repos/clockwork`: standard shared scheduler utility for cron and systemd manifests across other repos
- `./util-repos/tachometer`: standard shared profiling utility for repo snapshots, resource measurements, and profiled command runs across other repos
- `./util-repos/nordility`: standard VPN-switching utility for other repos and the strongest repo-level `AGENTS.md`
- `./util-repos/shock-relay`: standard external-messaging utility for other repos
- `./util-repos/short-circuit`: standard WireGuard VPN setup and configuration utility for other repos
- `./util-repos/snowbridge`: standard SMB-based file-sharing and phone-access utility for other repos
- `./util-repos/dyno-lab`: standard unified test bench utility — fixtures, subprocess/HTTP/env mocks, schema validation, smoke scaffolding, and pytest markers/fixtures
- `./util-repos/crew-chief`: standard local LLM inference utility — Podman-hosted Ollama service and zero-dependency Python client for trivial tasks across portfolio repos
- `./personal-finance`: strongest CI, test depth, and contributor workflow baseline
- `./research-repos/sonetsim`: strongest packaging and release alignment
- `./doc-repos/Certifications` and `./doc-repos/university-coursework`: strong examples of documentation-first repository organization

## Agent Operating Rules

1. For cross-repo work, read `traction-control/AGENTS.md`, `traction-control/CHATHISTORY.md`, and `traction-control/LESSONSLEARNED.md` first.
2. Then scan from the portfolio root (`../..` from this repo), not from the `traction-control` repo root.
3. Read the target repo’s `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` before making repo-specific changes.
4. Report the relevant prior history you relied on, and state when `CHATHISTORY.md` was updated.
5. Capture new durable lessons in `LESSONSLEARNED.md` when they should influence future sessions.
6. Never commit secrets, credentials, API keys, personal financial data, or local-only config files.
7. Do not modify files outside the repository you are explicitly working in unless the user asks for cross-repo work.
8. **Run the full local CI check suite before every push.** This is non-negotiable — do not push code that has not passed local verification. See the repo’s `AGENTS.md` "Local CI Verification" section for the exact commands. At minimum: `pre-commit run --all-files`; for Python repos also `pytest -q`.
9. After pushing changes that trigger GitHub Actions or other hosted CI, check the resulting workflow runs and resolve new failures before considering the work complete.
10. Use Conventional Commits for any git operations: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`.
11. Prefer additive, PR-ready changes. Do not rewrite history or remove user data unless explicitly instructed.
12. Preserve established architecture, naming, and folder conventions unless the task explicitly calls for restructuring.
13. When a repo contains architecture docs, diagrams, or workflow docs, keep them in sync with behavioral changes.

## Local CI Verification — traction-control

Run before every push:

```bash
pre-commit run --all-files
```

This repo has no Python source; `pre-commit` is the full verification gate.

Last reviewed: `2026-04-07`
