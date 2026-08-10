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
- Document the shared utility repos used across the portfolio for architecture toolchain bootstrap/render orchestration, Graphviz-backed diagram support, deterministic architecture-layout generation, agentic architecture authoring, password management, shared scheduling, repo and resource profiling, VPN switching, external messaging, and SMB-based file sharing.
- Provide the tiered bootstrap and scheduler entry point for cross-repo agents.

`traction-control` remains a policy-driven control plane rather than a
long-running application, but it now also provides a local orchestration entry
point. Its ignored master catalog can plan and materialize every repository in
the private/public registry without nesting their histories or exposing the
private inventory in tracked files. Its lifecycle reviewer maps tracked
repository dependencies and evaluates proposed privacy, archive, retirement,
and dependency-removal actions without applying remote changes.

Its private-sidecar contract separates tracked public-safe code from explicitly
selected ignored private data. L2 uses client-encrypted restic snapshots on a
private hosted SFTP target; L3 uses a strict-majority acknowledgement threshold
across at least three private-address SFTP targets intended for the WireGuard
mesh, with no hosted fallback. The coordinator does not verify WireGuard
membership, and a Restic acknowledgement alone remains insufficient. State v2
binds an encrypted portable manifest, while `drill` checks and restores every
recorded committed replica into a disposable owner-only spool before recording
no-overwrite evidence.

`portfolio_sidecar.py init-config` bootstraps owner-only, ignored local policy
and target files bound to the current registry. They start at generation zero
with no datasets or target sets; the command creates neither credentials nor
state, so enrollment and destination provisioning remain explicit operator
steps before `init-state`. Its `inventory-candidates` command creates a
generation-bound, metadata-only private review aid for registry-public desired
checkouts; candidates remain unenrolled until the operator explicitly adds
exact selectors to the ignored policy.

The separate Podman-on-WireGuard renderer bootstraps another inert owner-only
local document and can emit one inactive Linux target bundle at a time. Each
bundle binds the complete private target-document digest plus its selected mesh
generation and topology, contains one rootless key-only SFTP Quadlet plus one
persistent volume, and has no `[Install]` section. It never configures
WireGuard, creates secrets, exposes the Podman API, or activates services. Full
credential/governance validation, host-key pinning, and storage capacity
boundaries remain explicit activation gates. The coordinator remains the
existing static host-controlled writer; its container artifact is review-only
until policy-derived mounts, credentials, scheduling, and fencing are designed.

The tiered bootstrap downloads its allowlisted support repos and renders
Linux or macOS scheduler definitions around the existing workload scripts. The
effective runtime is still the documented governance loop: read the
control-plane docs, scan the portfolio root, inspect the target repo, use the
shared utility repos where appropriate, verify changes, and update the
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
- Create portfolio repositories through
  `scripts/create_private_github_repo.sh OWNER/REPO`. It always requests
  private visibility, verifies GitHub's response, and deliberately does not
  add a remote or push. Public visibility is a later, separately reviewed
  release action.

Its offline contract test is:

```bash
bash tests/test_create_private_github_repo.sh
```

## Key Files

- `AGENTS.md`: portfolio-wide agent instructions
- `CHATHISTORY.md`: local-only portfolio-wide session log
- `LESSONSLEARNED.md`: tracked durable lessons that should influence future sessions
- `docs/templates/SECURITY.md`: starter template for new repo security-policy files
- `docs/templates/LESSONSLEARNED.md`: starter template for new repo durable-lessons files
- `docs/lesson-capture-framework.md`: end-of-session gate for deciding whether a durable lesson must be recorded
- `CONTRIBUTING.md`: contribution guidelines for this control-plane repo
- `CHANGELOG.md`: notable changes to the portfolio-governance layer
- `scripts/install_podman_runtime.sh`: Podman CLI and rootless macOS machine bootstrap for container verification
- `scripts/create_private_github_repo.sh`: fail-closed private repository creation and post-create visibility verification
- `scripts/repository_visibility.py`: secure private/public registry validation, observed-transition reconciliation, hosted audit, and staged private-name disclosure gate
- `scripts/portfolio_materializer.py`: master registered-portfolio catalog, safe clone/fetch planning, additive registry-generation reconciliation, and checkout audit
- `scripts/portfolio_lifecycle_review.py`: read-only dependency evidence and proposed privacy/archive/retirement review
- `scripts/portfolio_sidecar.py`: standalone, fail-closed local-config bootstrap, metadata-only candidate inventory, portable-manifest backup, and exact restore-drill coordinator for explicitly selected ignored data
- `scripts/render_portfolio_sidecar_quadlets.py`: owner-only generation-zero bootstrap and render-only, one-node-at-a-time Linux Quadlet builder for mesh SFTP targets
- `containers/portfolio-sidecar-sftp/`: owned key-only OpenSSH/SFTP target image contract for a rootless Podman node
- `docs/repository-visibility.md`: private-first classification and observed-transition policy
- `docs/portfolio-lifecycle.md`: master checkout, dependency, retirement, and consistency architecture
- `docs/private-sidecar.md`: three-level ignored-data selection, encryption, acknowledgement, portable restore proof, and manual-failover contract
- `docs/podman-on-wireguard-sidecar.md`: native-Linux/WireGuard boundary, node-specific render workflow, and deliberately unimplemented activation gate
- `config/portfolio-sidecar/*.example.json`: synthetic sidecar policy, target, and Podman-mesh schemas; operational `*.local.json` files stay ignored
- `scripts/install_traction_control_agents.sh`: cross-platform tiered support-repo and agent scheduler bootstrap
- `config/traction-control-agents/repos.conf`: cumulative public support-repository bundles for the three profiles
- `scripts/render_private_bundle_overlay.sh`: fail-closed, registry-verified private support-repo overlay merged into an owner-only local bundle
- `config/traction-control-agents/private-repos.example.conf`: synthetic private-overlay template; the operational `*.local.conf` stays ignored
- `config/traction-control-agents/jobs.conf`: cumulative job membership, runtime environment, and schedule data
- `scripts/run_traction_control_job.sh`: launchd runtime adapter for local env files, startup delay, and jitter
- `scripts/bug_sweep_agentic.sh`: unattended daily review of clean code repos for potential bugs and regressions
- `scripts/check_github_push_ci.sh`: reusable GitHub Actions sweep for batches of pushed commits
- `scripts/ci_repair_agentic.sh`: unattended scan of default-branch GitHub Actions failures plus agentic repair handoff for clean repos
- `scripts/monitor_github_ci_emails.py`: Gmail inbox monitor for GitHub Actions failure notification emails
- `scripts/tachometer_disk_pressure_agentic.sh`: unattended tachometer disk-pressure remediation handoff for clean candidate repos
- `scripts/install_tachometer_disk_pressure_agentic_systemd.sh`: `clockwork` installer for the disk-pressure remediation timer
- `scripts/template_consolidation_agentic.sh`: unattended review pass that scans repo `SECURITY.md` and `LESSONSLEARNED.md` files for guidance worth promoting into the shared templates

## Control-Plane Flow

1. Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` here first.
2. Scan the portfolio root at `../..` to identify the current repo landscape.
3. Read the target repo's `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md`.
4. Apply standards or repo-specific changes, using shared utility repos such as
   `archility`, `auto-pass`, `clockwork`, `tachometer`, `nordility`,
   `shock-relay`, and `snowbridge` when they are the designated implementation
   homes.
5. Run repo-appropriate verification and, after pushes, check hosted workflow
   results when CI is involved.
6. Update `CHATHISTORY.md` and `LESSONSLEARNED.md` wherever the work produced
   new continuity or durable guidance.
7. Before the final response, run the lesson-capture gate in
   `docs/lesson-capture-framework.md` and report either the lesson file updated
   or why no durable lesson was added.

## Shared Utility Repos

- `./util-repos/auto-pass`: portfolio-standard password management and KeePassXC-backed secret helper
- `./util-repos/archility`: portfolio-standard architecture toolchain bootstrap/render orchestrator plus Graphviz-backed diagram support, deterministic starter-layout generation, agentic architecture authoring, and blueprint/drift-check help
- `./util-repos/clockwork`: portfolio-standard shared cron and `systemd` scheduling helper
- `./util-repos/tachometer`: portfolio-standard shared repo and resource profiling helper
- `./util-repos/nordility`: portfolio-standard NordVPN switching/orchestration helper
- `./util-repos/shock-relay`: portfolio-standard external messaging integration repo
- `./util-repos/snowbridge`: portfolio-standard SMB-based private file-sharing and phone-access helper
- `./util-repos/session-control`: portfolio-standard local AI-session inventory, resume-command, and cleanup helper

Private shared utilities are resolved through the ignored local registry and
catalog. They are intentionally not named or located in this tracked public
document.

## Architecture Layout Standard

Across the portfolio, the starter architecture surface should now be consistent:

- `docs/contributor-architecture-blueprint.md`
- `docs/diagrams/repo-architecture.puml`
- `docs/diagrams/repo-architecture.drawio`

`archility` is the standard place to generate and render that layout.
Its deterministic programmatic path creates the baseline starter strictly from code/layout markers, and its agentic path is where an AI agent should inspect a repository in depth and then author a unique architecture from that understanding.

## Contributing

See `CONTRIBUTING.md`.

## Tiered Agent Bootstrap

Use the bootstrap to download missing allowlisted support repositories and
render a cumulative agent profile:

```bash
bash scripts/install_traction_control_agents.sh --list-tiers
bash scripts/install_traction_control_agents.sh --tier light --dry-run
bash scripts/install_traction_control_agents.sh \
  --tier moderate \
  --provider auto \
  --model MODEL_NAME
```

`--tier` is required. By default, the command clones missing support repos and
writes scheduler artifacts under
`~/.local/share/traction-control/bootstrap/rendered/`, but leaves them
inactive. This render-only default can therefore change the filesystem and use
the network. Use `--dry-run` to preview repository, render, and reconciliation
actions without writes, clones, or service-manager calls. Use `--activate` only
after reviewing that preview when the selected jobs should become live.

Profiles control installed capability, not a read/write permission level.
Activated light jobs can make a narrowly warranted fix, moderate jobs can edit
policy/reference/architecture files and commit where their prompts allow, and
heavy disk remediation can commit and push validated changes. Activation can
also queue interval or overdue persistent jobs after their configured delay and
jitter. `--enable-autonomous-ci-repair` expands CI scheduling further; it is not
the only workload with write authority.

The profiles are cumulative:

| Profile | Support repos | Jobs added at this profile |
|---|---|---|
| `light` | `traction-control`, `clockwork` | Daily portfolio audit, review-first bug sweep, read-only CI discovery |
| `moderate` | Adds `archility`, `tachometer` | Daily and twice-weekly architecture checks, template consolidation, weekly REFS audit |
| `heavy` | Adds `auto-pass`, `shock-relay` | Tachometer disk-pressure remediation and an on-demand CI repair service |

Support-repo selection is not a target-repository allowlist. Discovery
workloads apply their own depth, exclusion, clean-worktree, and eligibility
rules beneath `PORTFOLIO_ROOT`. For example, a light bug sweep can review an
eligible existing repo outside the two light support repos. The profile
controls which supporting tools are present and which jobs are installed, not
which local repos those jobs may inspect.

### Private support repos

The tracked `config/traction-control-agents/repos.conf` can only ever name
public repositories: it is a tracked file in a public repo, so the private-name
disclosure gate treats a private slug there as a leak. Private support repos
use a second deployment workflow instead. Their membership lives in an ignored,
owner-only overlay that is verified against the private visibility registry and
merged with the tracked base into a local bundle:

```bash
bash scripts/render_private_bundle_overlay.sh --init   # starter from the registry
# uncomment the repos to enroll, then:
bash scripts/render_private_bundle_overlay.sh
bash scripts/render_private_bundle_overlay.sh --list   # membership per profile
```

`--init` writes every private-registry repository as a **commented-out**
candidate, so nothing is enrolled until the operator uncomments it — the same
opt-in posture as `portfolio_sidecar.py inventory-candidates`. The overlay
defaults to
`${XDG_CONFIG_HOME:-$HOME/.config}/traction-control/private-repos.local.conf`,
outside any repository; the rendered merged bundle lands beside it as
`repos.local.conf`. Both are written `0600`.

The renderer is fail-closed. It refuses a slug the registry classifies as
public (that belongs in the tracked base) or unclassified (record it first with
`scripts/create_private_github_repo.sh`), an overlay that Git tracks or that
sits unignored inside a work tree, a name that collides with the tracked base,
a malformed line, an unknown profile, and an overlay that enrolls nothing.

Pass the merged bundle to the installer. Private repos need SSH or an
authenticated HTTPS credential helper on machines where they are not already
checked out; where the checkout exists, the installer only verifies its origin
slug:

```bash
bash scripts/install_traction_control_agents.sh \
  --tier light \
  --repo-config ~/.config/traction-control/repos.local.conf \
  --clone-protocol ssh
```

Re-render after every change to the tracked base bundle — the merged file is
generated from the current base, so it cannot drift. Its offline contract test
is:

```bash
bash tests/test_render_private_bundle_overlay.sh
```

Heavy remains discovery-first by default. It installs
`ci-repair-agentic-repair.service` as an on-demand worker while retaining the
read-only discovery schedule. Start that worker only after discovery has
produced a candidate inventory:

```bash
# After discovery has produced a candidate inventory:
systemctl --user start ci-repair-agentic-repair.service
launchctl kickstart "gui/$(id -u)/io.github.casonk.traction-control.ci-repair-agentic-repair"
```

Use the command for the active platform; the LaunchAgent must already have been
loaded with `--activate`.

To deliberately grant scheduled broad repair authority, add:

```bash
bash scripts/install_traction_control_agents.sh \
  --tier heavy \
  --enable-autonomous-ci-repair \
  --activate
```

That option is valid only for `heavy`; it replaces the discovery-only CI timer
in the selected job set with the full autonomous CI-repair timer. The repair
agent may edit, commit, and push clean candidate repos according to its prompt,
so this is a separate authority decision from selecting the heavy support
bundle.

Platform output is selected automatically, or explicitly with `--platform`:

- Linux renders `clockwork` user units. Inactive output goes below the
  bootstrap state directory; `--activate` writes to
  `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user` and enables the selected timers.
- macOS renders native `io.github.casonk.traction-control.*` LaunchAgent plists.
  Inactive output goes below the bootstrap state directory; `--activate` writes
  to `~/Library/LaunchAgents` and loads the selected agents with `launchctl`.
  `scripts/run_traction_control_job.sh` loads optional local job env files and
  applies the configured startup delay and jitter before execution.

Reapplying an activated profile disables/unloads and archives known managed
jobs that are not selected, so a heavy-to-light transition does not leave
heavy authority active. It refuses to reconcile a workload that is already
running or starting. Render-only transitions reconcile only their inactive
artifacts.
Backups and the moderate/heavy Archility shim live below the bootstrap state
directory; do not delete it or point an activated profile at ephemeral storage.
Render-only output is rejected in the live scheduler directories.

Before activation, install Python 3.10+ for Clockwork and Python 3.11+ for
Archility, plus `gh`, `jq`, and one supported agent provider. The bootstrap
checks command presence, while provider authentication/model availability and
GitHub authentication are validated when the workloads run. On macOS, local
job env files are parsed as data (not sourced), must be owned by the current
user, and must not be writable by group or others.

Existing git checkouts are not pulled, reset, or rewritten. Their `origin`
slug is verified against the tracked allowlist on every run, and a
non-git path at an expected checkout location is treated as an error. Use
`--no-clone` to require all support repos to exist already, `--no-scheduler` for
repo setup only, and `--clone-protocol ssh` when SSH remotes are preferred by
the agent-profile installer. The separate portfolio materializer defaults to
SSH because private HTTPS credential helpers are intentionally excluded with
global Git config.
The tracked profile data lives in
`config/traction-control-agents/{repos,jobs}.conf`; the bootstrap also accepts
explicit config and absolute output-directory overrides for controlled testing
or local extensions.

### Container verification

Podman is the preferred container engine. Preview the host setup, install and
prepare Podman, then run every Linux profile in its own disposable container:

```bash
bash scripts/install_podman_runtime.sh --dry-run
bash scripts/install_podman_runtime.sh
bash tests/test_install_traction_control_agents_containers.sh
```

On macOS, the automated bootstrap uses Homebrew, creates a named rootless
Podman machine only when it is missing, and leaves the default Podman
connection unchanged. If you prefer Podman's upstream signed installer,
install it first and rerun the bootstrap with `--no-install`. The automated
Homebrew path is gated to its current support floor: Apple Silicon and macOS
13 or newer. Existing machines are not resized, reset, removed, or changed to
rootful/rootless mode. Podman machine's default macOS configuration exposes the
login user's home directory to the VM. Pass `--smoke-test` only when an image
pull and test-container run are wanted. When using a custom `--machine-name`,
give that same name to the test runner as
`TRACTION_CONTROL_PODMAN_CONNECTION`.

The harness stages only the required traction-control files plus the real
local Clockwork and Archility source trees, then builds one Linux test image.
Each light, moderate, and heavy container has networking disabled and a
read-only root filesystem. It downloads the selected support bundle from local
fixture remotes through real Git clones, renders the exact expected systemd
unit set with Clockwork, and validates every unit in user scope with
`systemd-analyze`. Verification gets a private ephemeral runtime directory but
no D-Bus or live user manager. The harness then checks an idempotent rerun and
confirms that `--activate` fails before writes when the container has no
systemd user manager.

This test does not execute agent workloads or fake a successful live
activation. A true activation integration test requires a disposable
systemd-booted environment with a dedicated user manager and all workload
services runtime-masked. See `tests/containers/README.md` for engine and path
overrides.

## Operational Scripts

All unattended agentic jobs in this repo follow the same runtime pattern:

- provider default comes from the job-specific `*_PROVIDER` env var
- model default comes from the matching `*_MODEL` env var
- optional local-only credential/profile overrides live in
  `~/.config/traction-control/<job-name>.env`
- `auto` provider mode now runs a CLI auth/status check plus a lightweight
  model-specific readiness probe before the real maintenance prompt starts, so
  an over-quota or unavailable provider is skipped up front instead of being
  discovered after the full job launches
- install scripts accept `--provider` and `--model`, and the `clockwork` web UI
  can edit the tracked provider/model defaults for the example manifests

For the every-other-day agentic template-consolidation pass, use:

```bash
bash scripts/template_consolidation_agentic.sh
```

The wrapper prefers `codex`, then `claude`, then `copilot` when
`TEMPLATE_CONSOLIDATION_PROVIDER=auto` (the default), but only after the
status/readiness precheck passes for the requested model. It refuses to run
when tracked `SECURITY.md` or `LESSONSLEARNED.md` files are already dirty,
unless you pass `--force`.

To install the user-level systemd timer through `clockwork`, use:

```bash
bash scripts/install_template_consolidation_agentic_systemd.sh --provider auto --model gpt-5.4
```

For the daily agentic bug-sweep pass, use:

```bash
bash scripts/bug_sweep_agentic.sh
```

The wrapper inventories clean code-focused repos, skips dirty worktrees by
default, and runs a findings-first review. It is review-first by default, so
target-repo edits are treated as exceptional rather than the normal outcome.

To install the user-level systemd timer through `clockwork`, use:

```bash
bash scripts/install_bug_sweep_agentic_systemd.sh --provider auto --model gpt-5.4
```

For the every-other-day agentic CI-repair pass, use:

```bash
bash scripts/ci_repair_agentic.sh
```

The wrapper inventories the latest default-branch CI across clean GitHub
repos, skips dirty worktrees by default, and only invokes an agent when one or
more repos are currently failing.

To install the user-level systemd timer through `clockwork`, use:

```bash
bash scripts/install_ci_repair_agentic_systemd.sh --provider auto --model gpt-5.4
```

For the read-only discovery half of that workflow, use:

```bash
bash scripts/ci_repair_agentic.sh --discovery-only
```

This writes the current inventory and candidate TSV files under
`~/.local/share/ci-repair-agentic/` and exits before launching any repair
agent. It is the preferred first step when separating GitHub Actions discovery
from broad multi-repo write authority.

To render and install the discovery-only timer through `clockwork`, use:

```bash
bash scripts/install_ci_repair_agentic_discovery_systemd.sh --provider auto --model gpt-5.4
```

When discovery finds candidates and you want to run the repair half explicitly,
use:

```bash
bash scripts/ci_repair_agentic_repair.sh \
  --candidate-file ~/.local/share/ci-repair-agentic/latest-candidates.tsv \
  --inventory-file ~/.local/share/ci-repair-agentic/latest-inventory.tsv
```

To install that explicit repair worker as an on-demand user service, use:

```bash
bash scripts/install_ci_repair_agentic_repair_systemd.sh --provider auto --model gpt-5.4
```

This installs `ci-repair-agentic-repair.service` only. It does not enable a
timer, so the repair half stays manual until its write-auth boundary is finalized.
Pass `--render-only --unit-dir /tmp/<dir>` when you want to inspect the unit
without touching the live user systemd tree.

For tachometer-triggered disk-pressure remediation, use:

```bash
bash scripts/tachometer_disk_pressure_agentic.sh --dry-run
```

The wrapper scans repo-local `.tachometer/backlog.json`,
`.tachometer/host-backlog.json`, `.tachometer/summary.json`, and
`.tachometer/host-summary.json` files across the portfolio. It exits without an
agent when no disk pressure is present, skips dirty worktrees by default, and
only hands clean candidate repos to the agent. The standard remediation pattern
is reversible repo-local archive automation for local-only caches, generated
artifacts, temporary downloads, and debug snapshots.

To install the user-level systemd timer through `clockwork`, use:

```bash
bash scripts/install_tachometer_disk_pressure_agentic_systemd.sh --provider auto --model gpt-5.4
```

For Gmail-based GitHub Actions failure monitoring, use:

```bash
python3 scripts/monitor_github_ci_emails.py
```

The monitor scans the configured Gmail inbox for GitHub notification emails
from `notifications@github.com` whose subject contains `Run failed:`, parses
the repo/workflow/run metadata, dedupes detections through a local JSON state
file, marks handled failure emails read, moves them to the configured
processed Gmail folder, and emits `WARNING ...` log lines for newly detected
failures. This keeps the job
compatible with `clockwork`'s warning surfacing without needing another alert
channel.

If `GITHUB_CI_FIXED_NOTIFY_TO` is set, the same monitor also checks previously
recorded failures against current GitHub Actions status through `gh run list`.
It sends a Gmail notification when a later default-branch SHA has completed
green, then marks that failure as fixed in the local state file so the resolved
email is sent once.

Set `GITHUB_CI_FIXED_NOTIFY_DIGEST=1` to queue those fixed-CI notices into the
shared shock-relay Gmail digest instead of sending one email per fixed repo.

Monitor-generated fixed-CI emails use `X-Portfolio-Service: traction-control`.
If they appear in the monitored inbox, the monitor leaves them there for the
configured grace window so Gmail/device notifications can fire, then moves them
to the configured notify folder without marking them read.

When `GITHUB_CI_EMAIL_TRIGGER_REPAIR=1`, each newly detected failure email also
schedules a delayed user-systemd trigger for
`ci-repair-agentic-discovery.service`. The
default delay is 30 minutes so an agent or human that just pushed a change has
time to finish before the autonomous repair sweep starts. Duplicate failure
emails coalesce behind one transient trigger unit.

The default Gmail config path is the sibling
`./util-repos/shock-relay/services/gmail-imap/config.local.yaml`. Override any
runtime settings through the optional local-only env file:

```text
~/.config/traction-control/github-ci-email-monitor.env
```

Useful overrides include `GITHUB_CI_EMAIL_GMAIL_CONFIG`,
`GITHUB_CI_EMAIL_STATE_FILE`, `GITHUB_CI_EMAIL_MAILBOX`,
`GITHUB_CI_EMAIL_SINCE_DAYS`, `GITHUB_CI_EMAIL_UNSEEN_ONLY`, and
`GITHUB_CI_EMAIL_PROCESSED_LABEL`. Fixed-run emails are opt-in with
`GITHUB_CI_FIXED_NOTIFY_TO`, can be aggregated with
`GITHUB_CI_FIXED_NOTIFY_DIGEST=1`, and their subject prefix can be changed with
`GITHUB_CI_FIXED_NOTIFY_SUBJECT_PREFIX`. One-off fixed-notification runs can be
narrowed with `GITHUB_CI_FIXED_NOTIFY_REPO` or `--fixed-notify-repo`.
Notify-folder routing uses
`GITHUB_CI_EMAIL_NOTIFY_LABEL` and `GITHUB_CI_EMAIL_NOTIFY_GRACE_MINUTES`.
Delayed repair scheduling uses `GITHUB_CI_EMAIL_TRIGGER_REPAIR`,
`GITHUB_CI_EMAIL_TRIGGER_REPAIR_DELAY_MINUTES`,
`GITHUB_CI_EMAIL_TRIGGER_REPAIR_SERVICE`, and
`GITHUB_CI_EMAIL_TRIGGER_REPAIR_UNIT`.
The default folders are `GitHub/CI/processed` and `GitHub/CI/notify`.

To install the user-level systemd timer through `clockwork`, use:

```bash
bash scripts/install_github_ci_email_monitor_systemd.sh
```

For batch post-push GitHub Actions checks, use:

```bash
bash scripts/check_github_push_ci.sh --input /path/to/pushes.tsv
```

The input file is tab-separated with columns:

```text
repo_rel	repo_slug	branch	sha
util-repos/traction-control	casonk/traction-control	main	e272b52
```

The script polls matching push-triggered workflow runs, prints one TSV result row
per commit, and exits nonzero if any run fails, times out, or the input is invalid.

## License

MIT
