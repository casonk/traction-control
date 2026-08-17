# Contributor Architecture Blueprint

This document maps the real control-plane workflow implemented by
`traction-control`. Unlike the feature repos, this repository is primarily
policy and continuity documentation. It now also provides an ignored private
portfolio authority, a checkout materializer, a read-only lifecycle reviewer,
an ignored-data sidecar, and a local bootstrap that composes the existing
workload scripts into inactive or activated Linux and macOS schedules; it is
still not a long-running application binary. It also has a narrowly scoped
Air-primary render coordinator that composes sibling-owned native macOS
renderers without activating them.

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
   - `scripts/portfolio_sidecar.py` implements the standalone fail-closed
     coordinator, while `docs/private-sidecar.md` defines explicit L1/L2/L3
     data selection. L2 writes client-encrypted restic snapshots to private
     hosted SFTP; L3 requires a strict-majority acknowledgement threshold
     across at least three private-address SFTP targets intended for the
     WireGuard mesh and has no hosted fallback. `init-config` creates an inert
     owner-only local pair, and `inventory-candidates` produces a metadata-only
     advisory report without enrolling or reading candidate contents.
     Policy/targets schema v1 does not prove peer membership. State schema v2
     binds an encrypted portable manifest; `drill` checks and exactly restores
     the committed snapshot ID for each replica recorded in state before
     writing owner-only, no-overwrite evidence bound to the control-plane state.
   - `scripts/render_portfolio_sidecar_quadlets.py` adds a separate inactive
     deployment boundary for L3 storage nodes. It binds an owner-only local
     document to the authoritative registry/target generation, strict-majority
     threshold, and exact topology, then renders only one native-Linux target
     Quadlet and volume per invocation. The owned SFTP image is key-only and
     rootless; WireGuard remains host-owned, `[Install]` is absent, and the
     coordinator artifact has a deliberately non-Quadlet review suffix.
   - The first executable sidecar remains a statically selected standalone
     coordinator with no prune path. It stores only latest committed state and
     does not implement repair, history, application-specific recovery, or
     automatic failover; those wait for a quorum-backed rqlite/Raft authority
     and later automation.
3. Temporary Air-primary render layer (`scripts/render_air_primary.py`)
   - An ignored owner-only local file pins the current Air `utun` allocation,
     Air/mini/pro RFC 1918 `/32`s, exact sibling checkouts, and reviewed paths
     and ports. The tracked example contains placeholders only.
   - The coordinator probes the current Clockwork and wiring-harness CLI
     contracts plus Snowbridge's SMB CLI when that native slice is explicitly
     enabled, then stages private inputs in one immutable generation and
     invokes the selected original sibling entry points unchanged.
   - Clockwork stays on `127.0.0.1:5001` behind the Air-IP `:8443` mTLS edge.
     The optional Snowbridge web role stays absent unless its exact loopback
     `:8080` backend is explicitly enabled. Native SMB review-plan rendering is
     controlled by a separate required boolean and, when enabled, remains below
     Snowbridge's ignored artifact boundary. Disabling native SMB omits its
     prerequisites and artifacts without changing the web role.
   - Outputs remain render-only. No `sudo`, launchd activation, Caddy start,
     SMB/PF mutation, key generation, WireGuard change, or writer failover is
     implemented.
4. Bootstrap profile layer (`scripts/install_traction_control_agents.sh`)
   - `config/traction-control-agents/repos.conf` defines the cumulative support
     repos for the light, moderate, and heavy profiles.
   - `config/traction-control-agents/jobs.conf` defines cumulative job
     membership, platform schedule data, provider/model env names, and the
     corresponding Linux installer.
   - The default flow clones missing support repos and renders inactive
     scheduler artifacts. `--dry-run` is the fully non-mutating preview, and
     `--activate` is the explicit live-scheduler boundary.
5. Scheduler adapter layer
   - On Linux, the bootstrap delegates rendering to the repo's `clockwork`
     installers and activates user timers only when requested.
   - On macOS, it delegates every plist render to Clockwork and routes
     executions through `scripts/run_traction_control_job.sh` and its Python
     adapter. Clockwork exclusively loads optional owner-only environment
     files. Native calendar triggers remain direct; interval jobs request a
     five-minute `RunAtLoad` poll whose locked 0700/0600 state applies the
     boot-relative delay once, retries failures no faster than the poll, and
     advances the original interval only after success. A wake after a missed
     interval yields one catch-up execution. Jitter and bounded network
     readiness are evaluated only after a due decision.
   - Activation pre-renders every selected plist, snapshots all managed plist,
     load, and disabled state, then transitions the profile. Any unload,
     install, enable, or bootstrap failure restores the snapshot. Render-only
     transitions reconcile only inactive output.
   - Activation refuses to replace a managed workload that is running or
     starting. The persistent bootstrap state directory owns backups and the
     Archility source-tree shim used by moderate/heavy jobs.
   - `scripts/install_refs_audit_agentic_systemd.sh` and
     `config/clockwork/refs-audit-agentic.toml.template` complete the Linux
     installer surface for the moderate REFS job.
6. Portfolio-boundary layer (`../..`)
   - This repo does not audit itself as the whole workspace.
   - Cross-repo work begins by scanning the portfolio root two levels up and
     then selecting the target repo from that inventory.
   - Profile repos are support dependencies, not workload targets or target
     allowlists. Discovery workloads apply their own depth, exclusion,
     clean-worktree, and eligibility filters below the portfolio root.
7. Shared-utility reference layer (`../archility`, sibling utility repos)
   - `archility` is the standard architecture toolchain home.
   - `clockwork` renders Linux scheduler units, while `tachometer` provides the
     resource signals consumed by disk-pressure remediation.
   - `auto-pass`, `nordility`, and `shock-relay` are the designated shared
     implementation homes for secrets, VPN switching, and external messaging.
   - The control plane advertises those repos so agents do not reimplement those
     capabilities ad hoc in other repos.
8. Governance execution loop
   - An agent reads the control-plane docs here first.
   - It then reads the target repo guidance, performs standards or repo-specific
     changes, runs verification, checks hosted workflows after pushes when
     applicable, and updates continuity files.
   - Heavy installs CI repair as an on-demand service by default. The separate
     `--enable-autonomous-ci-repair` flag replaces read-only CI discovery with
     the scheduled repair workflow and therefore expands write authority.
9. Self-validation layer (`.github/workflows/ci.yml`, `.pre-commit-config.yaml`)
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
   - `tests/test_air_primary_coordinator_podman.sh` stages only allowlisted
     coordinator and sibling-renderer sources into a networkless, read-only
     container. A namespace-local dummy `utun7` and synthetic credentials let
     the unchanged Clockwork, Snowbridge, wiring-harness, and Caddy validation
     paths prove successful rendering, immutable success, unsafe-inventory
     refusal, immutable failed-generation evidence, and native-SMB-off/web-on
     independence without host effects.
   - `tests/test_portfolio_sidecar_containers.sh` runs the synthetic sidecar
     suite in a networkless read-only Linux container. The opt-in
     `tests/test_portfolio_sidecar_real_podman.sh` creates one coordinator plus
     four disposable key-only OpenSSH/SFTP targets on an internal Podman
     network, initializes real Restic repositories, and verifies full and
     one-mesh-node-outage backup/restore-drill behavior without using live
     portfolio data or infrastructure.
   - `tests/test_portfolio_sidecar_quadlets.py` proves generation-zero and
     node-specific rendering remains owner-only, topology-bound, and inactive.
     `tests/test_portfolio_sidecar_sftp_image_podman.sh` builds the owned target
     image and exercises custom-port, pinned-key, forced-SFTP, read-only,
     minimal-capability, and persistent-volume behavior with disposable Podman
     resources. `tests/test_portfolio_sidecar_quadlet_generator_podman.sh`
     renders one inactive node and proves the real rootless generator accepts
     its container/volume dependency without creating runtime resources or a
     live service. macOS Podman Machine remains verification-only, not evidence
     of binding the Mac's WireGuard interface.
   - The CI job checks that the control-plane repo stays internally consistent,
     but it does not itself perform portfolio-wide maintenance.

## Key Entry Points

- `AGENTS.md`
- `LESSONSLEARNED.md`
- `CHATHISTORY.md`
- `README.md`
- `docs/repository-visibility.md`
- `docs/portfolio-lifecycle.md`
- `docs/private-sidecar.md`
- `docs/air-primary-render.md`
- `scripts/repository_visibility.py`
- `scripts/portfolio_materializer.py`
- `scripts/portfolio_lifecycle_review.py`
- `scripts/portfolio_sidecar.py`
- `scripts/render_portfolio_sidecar_quadlets.py`
- `scripts/render_air_primary.py`
- `config/repository-visibility/*.example.json`
- `config/portfolio/*.example.json`
- `config/portfolio-sidecar/*.example.json`
- `config/air-primary.example.toml`
- `containers/portfolio-sidecar-sftp/Containerfile`
- `scripts/check_portfolio_privacy.sh`
- `tests/test_repository_visibility.py`
- `tests/test_portfolio_materializer.py`
- `tests/test_portfolio_sidecar.py`
- `tests/test_portfolio_sidecar_hardening.py`
- `tests/test_portfolio_sidecar_containers.sh`
- `tests/test_portfolio_sidecar_real_podman.sh`
- `tests/test_portfolio_sidecar_quadlets.py`
- `tests/test_portfolio_sidecar_quadlet_generator_podman.sh`
- `tests/test_portfolio_sidecar_sftp_image_podman.sh`
- `tests/test_air_primary_coordinator_podman.sh`
- `tests/containers/Sidecar.Containerfile`
- `tests/containers/SidecarRealCoordinator.Containerfile`
- `tests/containers/SidecarRealSftp.Containerfile`
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
- `docs/diagrams/private-sidecar.puml`

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
- Keep sidecar selectors explicit, secrets out of Git, and L3 failover fenced;
  Gitignore rules alone grant no backup or read authority.
- Keep cross-repo renderers in their owning sibling repositories. A coordinator
  may stage private inputs and record hashes, but must not copy a security
  renderer or weaken its output boundary to make composition easier.
- Keep profile support repos distinct from portfolio targets, and keep
  autonomous CI repair behind its separate explicit opt-in.
- Update the blueprint and diagram sources together when the control-plane flow,
  shared utility set, verification requirements, or portfolio-scan boundary
  change.
