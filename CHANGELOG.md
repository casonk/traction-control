# Changelog

All notable changes to `traction-control` are documented here.

## Unreleased

- Add a "checkout currency" phase to `portfolio-audit.sh`: a checkout on its
  default branch that is behind origin, or on a work branch holding commits that
  are on no remote, is a gap. The REFS audit prompt now edits in a worktree and
  no longer leaves checkouts on local-only branches, which had stranded work in
  nineteen repositories.

- Add `scripts/check_repo_description.py` and a "repository description" check to
  `portfolio-audit.sh`: an empty description, filler wording such as "Repository
  for ...", or scaffold text is a gap, and a self-named opener, template or fork
  wording, or an over-long description is an advisory warning. The weekly REFS
  audit prompt now also reports descriptions that no longer match the README or
  `AGENTS.md` scope, without editing them. Replace this repository's own stale
  description.

- Add a "vault access" phase to `portfolio-audit.sh` that calls
  `auto-pass check-access` and counts every KeePass vault the login cannot read
  or write as a gap.

- Restore `common-words.example.json` to a synthetic placeholder and add a test
  that it stays one. The real list names private repositories and belongs in the
  ignored `common-words.local.json`; the privacy hook caught the leak.

- Let `job_guard.py install` leave a unit alone that already carries the guard's
  `ExecCondition=` (as rendered by clockwork's new `start_condition`), so the
  check does not run twice.

- Make the twice-weekly Archility job render diagrams in isolated clones and
  open diagram-only pull requests for registry-verified active repositories.
  Dirty checkouts are left untouched, existing Archility PRs are not
  duplicated, and merges remain manual.

- Add local model serving governance: AGENTS.md now makes the backend and
  settings each local model is served with a reviewed, evidence-backed
  decision held in crew-chief's serving policy, and `scripts/portfolio-audit.sh`
  runs `crew-chief policy check --live` to report drift between that policy
  and the models actually pulled.

- Fix the main-branch guard printing `cd: null directory` and `this operation
  must be run in a work tree` on every push from a bare repository: it now
  locates a bare repository by its Git directory, so bare clones outside
  temporary directories are guarded and scratch ones pass silently.
- Add repository settings as code: every repository declares its GitHub
  description and main-branch policy in `.github/repository.toml` (template in
  `docs/templates/repository.toml`), and `scripts/repo_settings.py check|apply`
  reports drift and applies the merged file to GitHub. Public repositories get
  the `main-branch-guard` ruleset (pull request required, no bypass).
- Add the local main-branch guard, `scripts/install_main_branch_guard.sh`:
  global pre-commit and pre-push hooks that refuse direct commits and pushes to
  `main`/`master` unless `ALLOW_MAIN=1` or the repository opts in. AGENTS.md
  rule 17 makes branch-and-pull-request the default for agents.

- Fix `scripts/realign_rewritten_checkouts.sh` skipping every real rewritten
  clone as "diverged with local-only commits": `grep -q` under `pipefail`
  turned a found tree into SIGPIPE failure on any non-trivial history.
- Add `scripts/realign_rewritten_checkouts.sh`, which moves the other clones
  of a rewritten portfolio (the Mac's, for example) onto the new history
  without losing local work: it resets only clones whose HEAD content exists
  on the new origin branch, keeps non-colliding local changes, and keeps the
  old HEAD as a local `legacy/pre-rewrite-<date>` branch. Repositories are
  discovered, so the tracked script names none.
- Backlog items keep Git-excluded material in a local attachment folder named
  after the item; the GitHub Support purge request moved there.
- AGENTS.md rule 13 now discloses AI assistance once per repository, in a
  standard `CONTRIBUTING.md` section (`docs/templates/CONTRIBUTING-ai-disclosure.md`),
  instead of per commit; `scripts/portfolio-audit.sh` reports a local
  `CONTRIBUTING.md` without it. Per-commit AI attribution trailers were
  removed from the published history of every portfolio repository that
  carried them.
- The backlog's GitHub Support purge item now covers all three history
  rewrites (2026-10-11, 2026-10-14 and 2026-10-15).
- Agentic jobs check, before contacting any provider, that no other agent is
  working in the checkout: a `codex`, `claude` or `copilot` process whose
  working directory is inside it, or any Git-visible file edited within the
  last 15 minutes, defers the run (exit 0, logged) or marks a repository
  `busy` in the inventory. A codex session editing the control-plane checkout
  for several hours on 2026-10-14 is the case this covers.
- A `local` provider runs codex against a model served by the local Ollama
  instance. It is opt-in (`AGENTIC_LOCAL_FALLBACK=1`) and is tried after
  `codex`, `claude` and `copilot` in `auto` mode.
- Agentic jobs in `auto` provider mode now fall back to the next ready
  provider (`codex`, then `claude`, then `copilot`) when the chosen one fails
  within two minutes of starting or reports a quota or rate limit. Before, a
  provider that passed its readiness probe and then failed on the real run
  ended the job, which is how the `--full-auto` removal went unnoticed.
- Portfolio scans (archility daily and weekly, the portfolio audit, and the
  agentic jobs) now count a directory as a repository only when its `.git`
  holds a `HEAD` file. An empty `.git` at the portfolio root, left behind as a
  sandbox mount point, made every scan treat the whole portfolio as one more
  repository: archility rendered diagrams into the portfolio root and timed
  out, and the agentic jobs listed the root as a candidate.
- `repository_visibility.py audit-private-disclosures --common-words FILE`:
  private repository names that are also ordinary words can be listed in an
  ignored local file. Such a name no longer matches as a word in prose but
  still matches as a slug, a path segment, or with `.git`. Each entry needs a
  reason and must name a private repository. `portfolio-audit.sh` passes
  `config/repository-visibility/common-words.local.json` when it exists.
- The six agentic job scripts now call `codex exec --sandbox workspace-write`
  instead of `codex exec --full-auto`. Newer `codex` releases removed
  `--full-auto` from `exec`, so every run since 2026-06-20 exited 2 at
  argument parsing, before the agent started.
- `scripts/job_guard.py install` now writes the time limit that matches the
  service type: `TimeoutStartSec=` for a oneshot unit, `RuntimeMaxSec=` for any
  other. It used to write both, and systemd logged "RuntimeMaxSec= has no
  effect in combination with Type=oneshot" for every guarded unit on each
  reload.
- Added `scripts/job_guard.py` (see `docs/job-guard.md`). `install` writes a
  systemd drop-in for every timer-driven user service that adds a start
  condition and, where the unit has none, a time limit. The condition defers a
  job while the CPU or GPU is hot, load is high, or another heavy job is
  running, and retries it later a bounded number of times. Jobs can be marked
  `light` or `exempt` in an optional local config. The guard fails open: an
  unreadable sensor or a broken config never stops a job.
- `scripts/repository_visibility.py record-public-observed`: record a
  repository that is already public on GitHub but in neither half of the
  registry. Until now the only way to register one was to edit the JSON pair by
  hand, because `record-private` writes only the private half and
  `reconcile-observed` needs an existing entry. The command observes
  github.com, requires the immutable ID and canonical slug to match exactly and
  the repository to be public, refuses any ID or slug already bound, and never
  changes a repository's visibility.
- `scripts/portfolio_purge.py` can now expire backup branches. With
  `--expire-backups-after-days`, a branch under a backup prefix (`legacy/` by
  default, `--backup-prefix` to change) is deleted once it is older than that,
  even though it holds content `main` lacks; until then it is kept and the log
  says how long it has left. Age comes from a trailing `YYYYMMDD` stamp in the
  branch name, else the tip commit date. The weekly schedule enables it at 90
  days. Pre-resync safety copies were otherwise permanent: they diverge from
  `main` by design, so no content test could ever call them finished.
- `scripts/portfolio_purge.py` now reports worktree directories that git no
  longer lists — left behind under `.claude/worktrees` when a repository is
  moved — on every run, as "needs attention", stating whether each one's files
  are all ignored or already in git history. They are reported, never deleted.
  Four such directories had gone unnoticed after a repository move because no
  git command lists them.
- Tidied `BACKLOG.md`: moved five completed items out of Pending into Done,
  grouped the SSH, ttyd and router items under the previously empty Network
  Exposure Hardening heading, and narrowed the publish-workflow item to
  `sonetsim`, the only one of its eight repos not yet migrated.
- Added `scripts/portfolio_purge.py` and a weekly schedule for it
  (`config/clockwork/portfolio-purge.toml`, wrapper `scripts/portfolio-purge.sh`).
  It removes local branches that `branch_inventory` classifies as `merged` or
  `redundant`, and worktrees that are clean, unlocked, on such a branch and
  older than an age guard. Everything else is kept and listed. It is a dry run
  unless `--apply` is given; deleting merged branches on `origin` and pruning
  dangling podman layers are opt-in. The first run cleared about 140 stale
  branches and worktrees from one workstation. Recorded the accompanying
  lesson, including that `git status` rewrites the index and so must not be
  used to judge a worktree's age.
- Added an owner-only, render-only Air-primary coordinator. It validates exact
  sibling worktrees and current CLI contracts, stages deterministic private
  inputs, invokes the unchanged Clockwork launchd, Snowbridge macOS SMB-plan,
  and wiring-harness mTLS edge renderers, and records immutable generation
  manifests without activating launchd, Caddy, SMB, PF, or networking. A
  networkless, read-only Podman proof runs all three real renderers plus Caddy
  validation and covers successful and failed generation immutability plus an
  unsafe wildcard-SMB inventory refusal. A required native-SMB boolean now
  preserves that path by default while allowing an explicit local disable to
  omit only SMB prerequisites, child rendering, logs, and artifacts; the
  independent Snowbridge web role still renders when separately enabled, and
  manifests record the disabled state and reason.
- Added a three-level private-sidecar control plane: tracked public-safe code,
  explicitly selected hosted-encrypted ignored data, and strict-majority
  mesh-only ignored data with no hosted fallback. The standalone coordinator
  now bootstraps inert owner-only local config, inventories ignored candidates
  without reading their contents, archives a portable encrypted manifest with
  every Restic snapshot, and performs exact committed-snapshot restore drills
  with no-overwrite evidence bound to state, policy, targets, and repository
  identity. Hostile-tree tests and a disposable real OpenSSH/Restic Podman
  topology exercise full and degraded quorum paths while automatic failover
  remains reserved for future quorum-issued leases and fencing tokens.
- Added explicit per-target SFTP ports and a render-only Podman-on-WireGuard
  deployment slice. It bootstraps inert local config, binds one-node-at-a-time
  inactive Quadlets to the complete private target-document digest and selected
  mesh topology, and includes an owned rootless key-only SFTP image. Disposable
  Podman proofs exercise the image and the real rootless Quadlet generator. No
  coordinator unit, WireGuard provisioning, Podman socket exposure, secret
  creation, or service activation is performed.
- Added a fail-closed GitHub repository-creation wrapper that always creates
  an empty private repository, verifies `isPrivate`, and keeps source, remote,
  push, and any later public release as separate reviewed actions.
- Fixed containerized user-unit verification by giving systemd 252 a private
  ephemeral runtime directory while keeping D-Bus absent, user scope explicit,
  generators disabled, and recursive verification errors fatal. Added a fast
  source-contract regression check for that boundary.
- Added a host-safe Podman bootstrap for macOS and Linux, including rootless
  macOS machine setup, explicit connection verification, dry-run and opt-in
  smoke-test modes, plus a Podman-first engine-neutral `Containerfile` harness.
- Added isolated Linux container tests for all three bootstrap profiles using
  real local Clockwork/Archility sources, offline Git remotes, exact unit-set
  assertions, `systemd-analyze`, idempotent reruns, and safe activation refusal
  when no systemd user manager is available.
- Added `install_traction_control_agents.sh` with cumulative light, moderate,
  and heavy support-repo/job profiles, an inactive render default, a fully
  non-mutating dry run, explicit activation, provider/model configuration, and
  guarded opt-in autonomous CI repair.
- Unified macOS job identity and plist rendering with Clockwork, moved private
  environment loading into Clockwork's owner-only no-follow loader, and added
  a stateful five-minute interval poll that preserves boot-relative startup,
  bounded failure retry, successful-run intervals, and one sleep catch-up.
  Launchd activation now validates all candidates before mutation and restores
  the prior managed plist/load/disabled state on a later transition failure,
  preserving absent versus explicit disabled-override entries exactly.
- Added tracked repository and job profile data plus native macOS LaunchAgent
  rendering, launch delay/jitter adaptation, Linux `clockwork` integration, and
  a missing weekly REFS audit installer/template.
- Made the selected portfolio workloads compatible with stock macOS Bash 3.2
  and BSD command-line utilities, and staggered the overlapping architecture
  schedules.
- Added fail-closed profile reconciliation with recoverable scheduler-artifact
  archives, live-directory guards, exact Git checkout validation, and a
  secure Clockwork-owned private environment-file loader on macOS.
- Added a reversible firewalld hardening workflow that removes blanket
  high-port allowances while preserving explicit WireGuard ingress.
- Added a reversible firewalld service-zone workflow that replaces unrestricted
  trusted-zone VPN access with an explicit private-service allowlist.
- Initialized `traction-control` as the portfolio control-plane repository.
- Migrated the portfolio-wide `AGENTS.md` and `CHATHISTORY.md` from the former workspace root into this repo.
- Rolled out the portfolio baseline files across the other repositories, including governance docs, architecture blueprints, and repo-appropriate CI where needed.
- Added a local CI workflow for `traction-control` so the control-plane repo validates its own baseline.
- Standardized `LESSONSLEARNED.md` as a tracked convention across the portfolio repositories.
- Added a control-plane rule to check post-push workflow results and treat new CI failures as part of the same rollout.
- Standardized a repo-level `AGENTS.md` reference back to `traction-control` so agents can find the shared portfolio conventions from any repo.
- Documented `auto-pass`, `nordility`, and `shock-relay` as shared portfolio utilities and propagated that guidance into repo-level `AGENTS.md` files.
- Added `archility` as the shared architecture inventory utility repo and updated the control-plane/shared-utility guidance to advertise it portfolio-wide.
- Updated the control-plane guidance so `archility` is also the shared home for architecture toolchain bootstrap and render orchestration, not just architecture audits.
- Standardized the starter architecture folder layout across the portfolio around `docs/contributor-architecture-blueprint.md` plus `docs/diagrams/repo-architecture.{puml,drawio}` and documented `archility` as the generator for that layout.
- Generated the shared architecture starter layout across the portfolio, then tightened the shared render validation around exact artifact filenames after correcting `archility`'s real toolchain behavior.
- Updated the shared starter PlantUML baseline to use Smetana layout after the first render pass showed Graphviz-dependent fallback images across many repos.
- Clarified that `archility` owns two architecture-authoring paths: a deterministic programmatic starter path and a non-deterministic agentic authoring path, while also keeping Graphviz support available for richer PlantUML diagrams.
- Added `clockwork`-backed scheduler manifests and installers for the shared `archility` audit/render jobs and the daily portfolio governance audit instead of keeping tracked repo-local unit files.
- Added the portfolio-wide `BACKLOG.md`, `REFS-PUBLIC.md`, and `REFS-LOCAL.md` conventions and documented `crew-chief`, `tachometer`, `bit-byte-block`, and `wiring-harness` as shared portfolio utilities.
- Added the daily governance audit flow that checks baseline files across the portfolio and now lints `SECURITY.md` files for private-reporting, disclosure, and sensitive-content policy coverage.
- Updated the shared `archility-weekly` job to a twice-weekly `Wed,Sun` schedule and aligned the related scripts, templates, and backlog guidance with that cadence.
- Ignored repo-local `__pycache__/` artifacts in the control-plane repo.
- Added the tachometer disk-pressure agentic remediation wrapper, prompt, and `clockwork` timer installer so disk red lights route to reversible repo-local archive automation.
- Added opt-in fixed-run email notifications to the GitHub CI failure email monitor.
- Changed the GitHub CI email monitor to file failure emails into a processed folder and generated fixed notifications into a notify folder after a grace window.
- Added delayed `ci-repair-agentic.service` scheduling when the GitHub CI email monitor detects new failure emails.
