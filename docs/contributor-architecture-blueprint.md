# Contributor Architecture Blueprint

This document maps the real control-plane workflow implemented by
`traction-control`. Unlike the feature repos, this repository is primarily
policy and continuity documentation. It now also provides an ignored private
portfolio authority, a checkout materializer, a read-only lifecycle reviewer,
and a local bootstrap that composes the existing workload scripts into
inactive or activated Linux and macOS schedules; it is still not a
long-running application binary.

## High-Level Layers

1. Control-plane policy layer (`AGENTS.md`, `LESSONSLEARNED.md`, `CHATHISTORY.md`)
   - `AGENTS.md` defines cross-repo operating rules, baseline standards, and
     portfolio priorities without tracking a name-by-name private inventory.
   - `LESSONSLEARNED.md` stores reusable operational lessons that should change
     future portfolio behavior.
   - `CHATHISTORY.md` is the local continuity log for cross-repo sessions.
2. Private portfolio authority and lifecycle layer
   - The ignored paired visibility registry is the authority for private/public
     classification and binds canonical slugs to immutable GitHub IDs.
   - The ignored master catalog binds those IDs to expected checkout paths and
     clone/fetch/manual/absence policy without nesting repositories as
     submodules.
   - `scripts/portfolio_materializer.py` validates, plans, materializes,
     fetch-synchronizes, audits, refreshes observed archive state, and
     additively reconciles registry-generation growth.
   - `scripts/portfolio_lifecycle_review.py` scans clean tracked repository
     evidence for dependencies and evaluates proposed make-private, archive,
     retire, and remove-dependency actions. It never applies them.
   - `scripts/repository_visibility.py` observes `github.com`, records an
     explicitly reviewed manual visibility/rename transition, and rejects
     private names in a public Git index.
   - Git/GitHub effects remain a fail-closed saga. Future quorum authority owns
     ACID intent, approvals, leases, and outbox state; WireGuard proximity does
     not choose a writer.
3. Bootstrap profile layer (`scripts/install_traction_control_agents.sh`)
   - `config/traction-control-agents/repos.conf` defines the cumulative support
     repos for the light, moderate, and heavy profiles.
   - `config/traction-control-agents/jobs.conf` defines cumulative job
     membership, platform schedule data, provider/model env names, and the
     corresponding Linux installer.
   - The default flow clones missing support repos and renders inactive
     scheduler artifacts. `--dry-run` is the fully non-mutating preview, and
     `--activate` is the explicit live-scheduler boundary.
4. Scheduler adapter layer
   - On Linux, the bootstrap delegates rendering to the repo's `clockwork`
     installers and activates user timers only when requested.
   - On macOS, it renders native LaunchAgent plists and routes executions
     through `scripts/run_traction_control_job.sh` for optional env loading,
     startup delay, and jitter.
   - Profile transitions disable or unload known unselected jobs during
     activation and archive their managed artifacts; render-only transitions
     reconcile only inactive output.
   - Activation refuses to replace a managed workload that is running or
     starting. The persistent bootstrap state directory owns backups and the
     Archility source-tree shim used by moderate/heavy jobs.
   - `scripts/install_refs_audit_agentic_systemd.sh` and
     `config/clockwork/refs-audit-agentic.toml.template` complete the Linux
     installer surface for the moderate REFS job.
5. Portfolio-boundary layer (`../..`)
   - This repo does not audit itself as the whole workspace.
   - Cross-repo work begins by scanning the portfolio root two levels up and
     then selecting the target repo from that inventory.
   - Profile repos are support dependencies, not workload targets or target
     allowlists. Discovery workloads apply their own depth, exclusion,
     clean-worktree, and eligibility filters below the portfolio root.
6. Shared-utility reference layer (`../archility`, sibling utility repos)
   - `archility` is the standard architecture toolchain home.
   - `clockwork` renders Linux scheduler units, while `tachometer` provides the
     resource signals consumed by disk-pressure remediation.
   - `auto-pass`, `nordility`, and `shock-relay` are the designated shared
     implementation homes for secrets, VPN switching, and external messaging.
   - The control plane advertises those repos so agents do not reimplement those
     capabilities ad hoc in other repos.
7. Governance execution loop
   - An agent reads the control-plane docs here first.
   - It then reads the target repo guidance, performs standards or repo-specific
     changes, runs verification, checks hosted workflows after pushes when
     applicable, and updates continuity files.
   - Heavy installs CI repair as an on-demand service by default. The separate
     `--enable-autonomous-ci-repair` flag replaces read-only CI discovery with
     the scheduled repair workflow and therefore expands write authority.
8. Self-validation layer (`.github/workflows/ci.yml`, `.pre-commit-config.yaml`)
   - This repo validates its own docs/config baseline with pre-commit.
   - `scripts/install_podman_runtime.sh` installs or verifies Podman and, on
     macOS, prepares a named rootless machine without changing the default
     connection. The container host runner prefers Podman and keeps Docker as
     a compatible fallback.
   - `tests/test_install_traction_control_agents_containers.sh` stages a narrow
     image context with this repo plus the real local Clockwork and Archility
     source trees. Separate networkless, read-only Linux containers validate
     light, moderate, and heavy downloads, exact rendered unit sets,
     strict user-scope `systemd-analyze` parsing through a private runtime with
     no user bus, idempotent reruns, and fail-before-write activation when no
     systemd user manager is available.
   - Ordinary containers do not prove live timer activation. That requires a
     disposable systemd-booted environment with a dedicated user manager and
     runtime-masked workload services.
   - Real-Git regression tests exercise catalog reconciliation, atomic
     materialization, fetch-only behavior, lifecycle dependency evidence,
     private-name disclosure checks, and fail-closed identity/path rules.
   - The CI job checks that the control-plane repo stays internally consistent,
     but it does not itself perform portfolio-wide maintenance.

## Key Entry Points

- `AGENTS.md`
- `LESSONSLEARNED.md`
- `CHATHISTORY.md`
- `README.md`
- `docs/repository-visibility.md`
- `docs/portfolio-lifecycle.md`
- `scripts/repository_visibility.py`
- `scripts/portfolio_materializer.py`
- `scripts/portfolio_lifecycle_review.py`
- `config/repository-visibility/*.example.json`
- `config/portfolio/*.example.json`
- `scripts/check_portfolio_privacy.sh`
- `tests/test_repository_visibility.py`
- `tests/test_portfolio_materializer.py`
- `scripts/install_traction_control_agents.sh`
- `config/traction-control-agents/repos.conf`
- `config/traction-control-agents/jobs.conf`
- `scripts/run_traction_control_job.sh`
- `scripts/install_refs_audit_agentic_systemd.sh`
- `config/clockwork/refs-audit-agentic.toml.template`
- `scripts/install_podman_runtime.sh`
- `tests/test_install_traction_control_agents_containers.sh`
- `tests/containers/Containerfile`
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
  executable automation. The bootstrap schedules the governance scripts, but
  those scripts still implement an agent-guided portfolio loop.
- Keep private names, checkout paths, reports, and plans in ignored owner-only
  files. Tracked architecture may describe the control flow, never the private
  inventory.
- Treat the materializer as a checkout orchestrator, not a full GitHub backup,
  and treat reviewed visibility/archive/deletion as external saga effects.
- Keep profile support repos distinct from portfolio targets, and keep
  autonomous CI repair behind its separate explicit opt-in.
- Update the blueprint and diagram sources together when the control-plane flow,
  shared utility set, verification requirements, or portfolio-scan boundary
  change.
