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
- `docs/templates/SECURITY.md`: starter template for new repo security-policy files
- `docs/templates/LESSONSLEARNED.md`: starter template for new repo durable-lessons files
- `CONTRIBUTING.md`: contribution guidelines for this control-plane repo
- `CHANGELOG.md`: notable changes to the portfolio-governance layer
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

## Shared Utility Repos

- `./util-repos/auto-pass`: portfolio-standard password management and KeePassXC-backed secret helper
- `./util-repos/archility`: portfolio-standard architecture toolchain bootstrap/render orchestrator plus Graphviz-backed diagram support, deterministic starter-layout generation, agentic architecture authoring, and blueprint/drift-check help
- `./util-repos/clockwork`: portfolio-standard shared cron and `systemd` scheduling helper
- `./util-repos/tachometer`: portfolio-standard shared repo and resource profiling helper
- `./util-repos/nordility`: portfolio-standard NordVPN switching/orchestration helper
- `./util-repos/shock-relay`: portfolio-standard external messaging integration repo
- `./util-repos/snowbridge`: portfolio-standard SMB-based private file-sharing and phone-access helper
- `./util-repos/session-control`: portfolio-standard local AI-session inventory, resume-command, and cleanup helper

## Architecture Layout Standard

Across the portfolio, the starter architecture surface should now be consistent:

- `docs/contributor-architecture-blueprint.md`
- `docs/diagrams/repo-architecture.puml`
- `docs/diagrams/repo-architecture.drawio`

`archility` is the standard place to generate and render that layout.
Its deterministic programmatic path creates the baseline starter strictly from code/layout markers, and its agentic path is where an AI agent should inspect a repository in depth and then author a unique architecture from that understanding.

## Contributing

See `CONTRIBUTING.md`.

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
file, applies a processed Gmail label after a successful live scan, and emits
`WARNING ...` log lines for newly detected failures. This keeps the job
compatible with `clockwork`'s warning surfacing without needing another alert
channel.

The default Gmail config path is the sibling
`./util-repos/shock-relay/services/gmail-imap/config.local.yaml`. Override any
runtime settings through the optional local-only env file:

```text
~/.config/traction-control/github-ci-email-monitor.env
```

Useful overrides include `GITHUB_CI_EMAIL_GMAIL_CONFIG`,
`GITHUB_CI_EMAIL_STATE_FILE`, `GITHUB_CI_EMAIL_MAILBOX`,
`GITHUB_CI_EMAIL_SINCE_DAYS`, `GITHUB_CI_EMAIL_UNSEEN_ONLY`, and
`GITHUB_CI_EMAIL_PROCESSED_LABEL`. The default processed label is
`GitHub/CI Failure Processed`.

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
