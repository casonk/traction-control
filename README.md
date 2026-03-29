# traction-control

Portfolio control-plane repository for cross-repository agent guidance, maintenance standards, and session continuity.

This repo lives under:

- `./util-repos/traction-control`

Its effective scan target is the portfolio root two levels up:

- `../..`

## Purpose

- Store the portfolio-wide `AGENTS.md` used for cross-repo maintenance work.
- Store the portfolio-wide `LESSONSLEARNED.md` used for durable cross-repo operational guidance.
- Store the portfolio-wide `CHATHISTORY.md` used for local session continuity.
- Define the baseline conventions for repositories under the portfolio root.
- Document the shared utility repos used across the portfolio for architecture toolchain bootstrap/render orchestration, Graphviz-backed diagram support, deterministic architecture-layout generation, agentic architecture authoring, password management, VPN switching, and external messaging.
- Act as the home repo for future cross-repo automation or inventory tooling.

The important implementation detail today is that `traction-control` is still a
policy-driven control plane, not a local orchestration binary. The effective
"runtime" is an agent or contributor following the documented governance loop:
read the control-plane docs, scan the portfolio root, inspect the target repo,
use the shared utility repos where appropriate, verify changes, and update the
continuity files.

## Working Rule

When auditing or maintaining the portfolio, scan from the portfolio root, not from the `traction-control` repo root.

Example:

```bash
PORTFOLIO_ROOT="$(cd ../.. && pwd)"
find "$PORTFOLIO_ROOT" -maxdepth 4 -type d -name .git | sort
```

## GitHub Publishing Notes

- GitHub CLI authentication may already be active for the workspace user; verify before starting a new login flow.
- An SSH key is available in the environment, so SSH remotes are a valid publishing path when creating or pushing the repo.

## Key Files

- `AGENTS.md`: portfolio-wide agent instructions
- `CHATHISTORY.md`: local-only portfolio-wide session log
- `LESSONSLEARNED.md`: tracked durable lessons that should influence future sessions
- `docs/templates/LESSONSLEARNED.md`: starter template for new repo durable-lessons files
- `CONTRIBUTING.md`: contribution guidelines for this control-plane repo
- `CHANGELOG.md`: notable changes to the portfolio-governance layer

## Control-Plane Flow

1. Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` here first.
2. Scan the portfolio root at `../..` to identify the current repo landscape.
3. Read the target repo's `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md`.
4. Apply standards or repo-specific changes, using shared utility repos such as
   `archility`, `auto-pass`, `nordility`, and `shock-relay` when they are the
   designated implementation homes.
5. Run repo-appropriate verification and, after pushes, check hosted workflow
   results when CI is involved.
6. Update `CHATHISTORY.md` and `LESSONSLEARNED.md` wherever the work produced
   new continuity or durable guidance.

## Shared Utility Repos

- `./util-repos/auto-pass`: portfolio-standard password management and KeePassXC-backed secret helper
- `./util-repos/archility`: portfolio-standard architecture toolchain bootstrap/render orchestrator plus Graphviz-backed diagram support, deterministic starter-layout generation, agentic architecture authoring, and blueprint/drift-check help
- `./util-repos/nordility`: portfolio-standard NordVPN switching/orchestration helper
- `./util-repos/shock-relay`: portfolio-standard external messaging integration repo

## Architecture Layout Standard

Across the portfolio, the starter architecture surface should now be consistent:

- `docs/contributor-architecture-blueprint.md`
- `docs/diagrams/repo-architecture.puml`
- `docs/diagrams/repo-architecture.drawio`

`archility` is the standard place to generate and render that layout.
Its deterministic programmatic path creates the baseline starter strictly from code/layout markers, and its agentic path is where an AI agent should inspect a repository in depth and then author a unique architecture from that understanding.

## Contributing

See `CONTRIBUTING.md`.

## License

MIT
