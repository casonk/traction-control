# BACKLOG.md

Portfolio backlog for this repository. Pending items are candidates for execution —
manually or via crew-chief. Entries sourced from archility audit are tagged
`[archility:YYYY-MM-DD]`; manual entries use `[manual:YYYY-MM-DD]`.

The archility twice-weekly job populates this file automatically via `archility audit --write-backlog`.
To execute a backlog item with crew-chief: `crew-chief agent "Work on item: <item text>"`.
Mark items `[x]` when complete and move them to Done.

## Pending

- [ ] [manual:2026-08-24] **Clear the 42 pre-existing private-name disclosures
  in public repositories** — The new disclosure sweep in `portfolio-audit.sh`
  found private repository names in tracked files across 10 public
  repositories, including whole tracked directories named after private repos
  under `examples/`. These predate the 2026-08-23 incident and were never
  detected because the audit only ever ran against this repository. Renaming a
  tracked example directory is a breaking change for anything referencing it,
  so sequence it per repo rather than sweeping. Per-repo detail is in the
  ignored local report. Note that scrubbing the working tree leaves the names
  in each repository's public history, same as below.

- [ ] [manual:2026-08-23] **Audit git history for the private repository names
  disclosed on 2026-08-23** — The convention-audit commit added private repo
  names to four tracked files in this public repository. The working tree is
  scrubbed and the gate that should have caught it is fixed, but the names
  remain reachable in this repository's public history. Decide whether that
  warrants a history rewrite. Note the constraints already recorded below:
  rewriting invalidates every gitleaks baseline fingerprint, and force-pushing
  a branch with an open PR closes it irreversibly. Detail is in the ignored
  local report, not here.

- [ ] [manual:2026-08-23] **Decide whether a repository outside
  `util-repos/`/`sec-repos/` is in or out of the baseline** — One portfolio
  repository is checked out directly under the portfolio root, so every sweep
  that enumerates those two directories misses it; it carries 18 Tier-1 gaps
  and is absent from the visibility registry and portfolio catalog. Either seed
  the baseline and register it, or record an explicit exemption. The current
  state is neither. Identify it from the ignored registry, not from here.

- [ ] [manual:2026-08-23] **Decide whether `.gitleaks.toml` without
  `secret-scan.yml` is acceptable** — Two repositories each ship a gitleaks
  config and baseline that nothing ever runs, so they read as scanned while
  nothing scans them. Either add the `secret-scan.yml` workflow (template in
  `docs/templates/secret-scan.yml`) or remove the dead config, and add a
  portfolio-audit check for the mismatch so it cannot recur silently.

- [ ] [manual:2026-07-19] **Add a disposable live-systemd activation test** —
  Extend the container/VM harness with a dedicated non-root user manager,
  writable test-only user unit directory, and runtime-masked managed services.
  Verify exact enabled/active timer sets and heavy-to-light plus normal-to-
  autonomous reconciliation without allowing any workload to execute.

- [ ] [manual:2026-07-19] **Add a first-class launchd backend to Clockwork** —
  Extend Clockwork's manifest schema and renderer to produce validated macOS
  LaunchAgents, including calendar/interval schedules, environment values,
  log paths, and activation lifecycle. Then replace traction-control's direct
  plist renderer while retaining its tier manifests and delay/jitter adapter.

### Reusable Workflow Migration

- [x] [manual:2026-06-17] **Tier 1** — Migrate the reviewed public repositories
  to reusable workflow callers. Private-repository migration and push details
  now live only in the ignored lifecycle report and plan.

- [x] [manual:2026-06-17] **Tier 2** — Add `skip-install` input to `python-ci.yml` reusable workflow,
  then migrate 5 pre-commit-only repos: traction-control, pit-box, short-circuit, snowbridge,
  wiring-harness. Add ruff check to pre-commit configs where applicable before migrating.

- [x] [manual:2026-06-17] **Tier 3** — Fix pyproject.toml `[dev]` extras then migrate 5 repos:
  `dyno-lab` (add ruff-format to [dev]); `nordility` (switch CI to pip install -e ".[dev]");
  `tachometer` (fix dyno-lab reference from PyPI name to git URL in [dev]);
  `citegres` (add networkx/matplotlib to [dev]); `zillow-public-data` (add deps to pyproject,
  handle PYTHONPATH). Add ruff check pre-commit hook to each before migrating.

- [x] [manual:2026-06-17] **Tier 5 — Per-repo decisions** (handle one at a time when ready):
  archility (unittest→pytest decision, PYTHONPATH=src, smoke test),
  auto-pass (smoke test, pinned tool versions),
  clockwork (drop redundant direct lint steps, then migrate),
  shock-relay (hybrid Python compile + ShellCheck — needs custom inline or new input),
  intake (pytest --tb=short → add pytest-args input, consolidate 2 jobs),
  fred-public-data (switch from requirements.txt to pyproject-based install),
  windshield (add ruff+pylint to [dev], add ruff hook to pre-commit). Private
  per-repository decisions are recorded in the ignored lifecycle plan.

- [x] [manual:2026-06-17] **Tier 6 — Major overhaul first** (handle when touching these repos):
  `sonetsim` — drop Python 3.8/3.9 (EOL), add [dev] extras, fix non-standard test paths;
  `pushshift_python` — resolve MPLCONFIGDIR env var need (conftest.py or new workflow input).

- [ ] [manual:2026-06-17] **Publish workflow migration** — Migrate `python-publish.yml` inline
  workflows to `casonk/.github/.github/workflows/python-publish.yml@main` for repos that have them:
  crew-chief, archility, auto-pass, clockwork, dyno-lab, nordility, tachometer, sonetsim.

- [ ] [manual:2026-06-17] **Secret-scan workflow migration** — Migrate `secret-scan.yml` inline
  workflows to `casonk/.github/.github/workflows/secret-scan.yml@main` across the portfolio
  after confirming each repo has `.gitleaks.toml` and `.gitleaks-baseline.json` in place.

- [ ] [manual:2026-06-21] **Repair SSH/Git host config permissions** — Investigate and fix
  the host-level SSH configuration issue causing Git pushes to fail with
  `Bad owner or permissions on /etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf`.
  Standardize the fix so normal `git push` and `ssh -T git@github.com` work
  without one-off `GIT_SSH_COMMAND` overrides.

- [ ] [manual:2026-06-26] **Server-side SSH hardening against DoS/brute-force** — On the
  Linux desktop SSH server, harden `sshd_config` and install connection rate limiting:
  (1) Set `ClientAliveInterval 60` / `ClientAliveCountMax 3` so stale server-side
  sessions are reaped (mirrors the client-side `~/.ssh/config` keepalive now in place).
  (2) Set `MaxAuthTries 3`, `LoginGraceTime 30`, `MaxStartups 10:30:100` to limit
  unauthenticated connection accumulation.
  (3) Install `fail2ban` (or configure `nftables` rate-limit rules) to ban IPs after
  repeated failed auth attempts — protect any port-forwarded SSH or WireGuard ingress.
  (4) Confirm `PasswordAuthentication no` and `PermitRootLogin no` in sshd_config.
  Related: see `util-repos/snowbridge` host-setup docs for the desktop's network/firewall context.

- [ ] [manual:2026-06-26] **ttyd web terminal hardening** — The session-control webterm
  integration (via `SESSION_CONTROL_WEBTERM_URL`) proxies to a ttyd instance. Harden it:
  (1) Confirm ttyd binds only to loopback or VPN interface, never 0.0.0.0 publicly.
  (2) Set `--max-clients 5` (or appropriate limit) so a connection flood cannot exhaust
  file descriptors.
  (3) Set `--ping-interval 30` in ttyd so idle websocket connections are reaped server-side.
  (4) Configure Caddy rate limiting (`rate_limit` directive or middleware) in front of the
  ttyd endpoint so a single IP cannot open more than ~10 connections per minute.

- [ ] [manual:2026-06-26] **Router DoS protection settings audit** — Review the Aterm
  WG1200CR's SPI firewall and DoS-mitigation knobs through the private
  router-automation checkout's `show.py`. If the router exposes configurable
  SYN-flood or port-scan detection settings (check `DEVICE.ADVMENU` and
  `INET.WAN-1` service XML), enable them via `hedwig.cgi` and add the patches
  to that checkout's `harden.py` so they survive PSK rotations.

- [ ] [manual:2026-06-17] **Private local-only repositories** — Review each
  ignored-catalog local checkout, create a private remote only through the
  private-first workflow when appropriate, and keep names, paths, and push
  status in the ignored lifecycle plan.

- [ ] [manual:2026-06-15] Add TMDB-backed watch suggestions to the clockwork
  to-watch page. Register for a free TMDB API key, then for each title in the
  library and watch list call `/movie/{id}/recommendations` and
  `/tv/{id}/recommendations`, deduplicate, rank by popularity, and surface
  results in a suggestion panel with poster, year, rating, and one-click "Add
  to list". Pairs with the existing Ollama suggestion panel as a higher-quality
  alternative.

- [ ] [manual:2026-06-13] Sign wiring-harness mobileconfig profiles with an
  Apple Developer certificate so iOS shows "Verified" rather than "Signed,
  Unverified". The `export_mtls_profile.py` script already accepts
  `--signing-cert` / `--signing-key`; just needs a Developer ID cert exported
  from Xcode/Keychain and the paths wired into the install invocation.

- [ ] [manual:2026-06-11] Add post-refresh archive hooks to the private data
  application identified in the ignored lifecycle plan so its existing
  `manage_storage_archives.py auto` coverage runs after successful
  scheduled/manual data refreshes, not only when disk pressure crosses the
  configured high watermark.
- [ ] [manual:2026-06-11] Decide and implement the post-refresh pruning policy
  for `research-repos/zillow-public-data`: the existing archive tool currently
  shows both restored `data/` and `.zillow-generated-archives/data.tar.gz`;
  choose whether refreshes should prune restored generated data immediately or
  leave pruning to disk-pressure automation.

- [ ] [manual:2026-06-15] Add tradility entry to clockwork — create a
  `GET /api/tradility-analysis` endpoint that reads
  `exports/tradility-analysis.json` and a `to-tradility.html` page that
  renders RSI and VWAP signals per ticker from the holdings aggregate.
  Backlog lives in `util-repos/tradility/BACKLOG.md`.

## In Progress

## Done

- [x] [manual:2026-08-29] **Add a privacy-safe portfolio backlog index** — Added an ignored, owner-only renderer that combines the existing visibility registry and portfolio catalog with canonical repository backlogs. It emits opaque item IDs, state, priority, and generic blocker classes, never canonical wording; optional reviewed safe titles remain local.

- [x] [manual:2026-06-26] **KeePass-via-snowbridge macOS integration** — Mount the
  snowbridge SMB share on the Mac and access server KeePass vaults through it.
  Added `config/keepass-snowbridge.example.env` (host/path config, no passwords),
  `scripts/setup_keepass_snowbridge.sh` (one-time setup: installs keepassxc via
  Homebrew, stores SMB credentials in macOS Keychain, wires auto-pass profiles),
  `scripts/mount_snowbridge.sh` (idempotent mount via Keychain credential lookup),
  and `scripts/unmount_snowbridge.sh`. Passwords never touch disk — Keychain only.
  `--host` override on mount allows one-off remote connections over WireGuard/Tailscale.

- [x] [manual:2026-06-11] Add stale-age archive rotation to
  `util-repos/fedora-debugg` for ignored `artifacts/snapshot-*` directories.
  Implemented reversible repo-local move rotation with a manifest and restore
  command, wired it into `run_workflow.sh`, and rotated 171 user-owned stale
  snapshots into `artifacts/archive/snapshots/`. Four `nobody:nobody` snapshots
  remain active because they need an ownership/elevated cleanup decision.
