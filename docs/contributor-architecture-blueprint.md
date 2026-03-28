# Contributor Architecture Blueprint

This document maps the real control-plane workflow implemented by
`traction-control`. Unlike the feature repos, this repository is mostly policy
and continuity documentation; the execution path is an agent-guided governance
loop rather than a local application binary.

## High-Level Layers

1. Control-plane policy layer (`AGENTS.md`, `LESSONSLEARNED.md`, `CHATHISTORY.md`)
   - `AGENTS.md` defines the cross-repo operating rules, repository landscape,
     baseline standards, and portfolio priorities.
   - `LESSONSLEARNED.md` stores reusable operational lessons that should change
     future portfolio behavior.
   - `CHATHISTORY.md` is the local continuity log for cross-repo sessions.
2. Portfolio-boundary layer (`../..`)
   - This repo does not audit itself as the whole workspace.
   - Cross-repo work begins by scanning the portfolio root two levels up and
     then selecting the target repo from that inventory.
3. Shared-utility reference layer (`../archility`, sibling utility repos)
   - `archility` is the standard architecture toolchain home.
   - `auto-pass`, `nordility`, and `shock-relay` are the designated shared
     implementation homes for secrets, VPN switching, and external messaging.
   - The control plane advertises those repos so agents do not reimplement those
     capabilities ad hoc in other repos.
4. Governance execution loop
   - An agent reads the control-plane docs here first.
   - It then reads the target repo guidance, performs standards or repo-specific
     changes, runs verification, checks hosted workflows after pushes when
     applicable, and updates continuity files.
5. Self-validation layer (`.github/workflows/ci.yml`, `.pre-commit-config.yaml`)
   - This repo validates its own docs/config baseline with pre-commit.
   - The CI job checks that the control-plane repo stays internally consistent,
     but it does not itself perform portfolio-wide maintenance.

## Key Entry Points

- `AGENTS.md`
- `LESSONSLEARNED.md`
- `CHATHISTORY.md`
- `README.md`
- `.github/workflows/ci.yml`
- `docs/diagrams/repo-architecture.puml`
- `docs/diagrams/repo-architecture.drawio`

## Regeneration

```bash
cd ../archility
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m archility render ../traction-control
```

## Contributor Notes

- Treat this file and the paired `docs/diagrams/` sources as the default
  architecture handoff surface for the control plane.
- Keep the distinction explicit between portfolio-governance policy and
  executable automation. Today the governance loop is agent-driven.
- Update the blueprint and diagram sources together when the control-plane flow,
  shared utility set, verification requirements, or portfolio-scan boundary
  change.
