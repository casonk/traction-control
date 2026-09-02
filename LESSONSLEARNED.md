# LESSONSLEARNED.md — Portfolio Control Plane

> Purpose: record durable lessons that should change how future agents work across the portfolio.
> Unlike `CHATHISTORY.md`, this file is tracked and should keep only reusable lessons.

## How To Use This File

- Read this file before repeating setup, publishing, audit, or automation workflows.
- Add lessons that generalize beyond a single session.
- Keep entries concise and action-oriented.
- Do not use this file for transient status updates or full session logs.

## Lessons

### 2026-09-02 — WireGuard/VPN installer and example conventions (portfolio-general)

Up-integrated from `short-circuit` and `snowbridge`, which maintained these
rules in parallel. Repo-specific phrasing (iPhone/SMB detail in snowbridge, the
generic client in short-circuit) stays in those repos; the general rule lives
here so agents read it once.

- WireGuard peer templates should generate missing key pairs coherently. The
  server private key pairs with the client public key, and the client private
  key with the server public key. When both placeholders for a pair are still
  present, the setup script should generate and write a coherent pair
  automatically instead of failing on a key it is already positioned to create.
- Treat checked-in sample values as incomplete config. Generic placeholder
  detection is not enough when an example uses a realistic sample such as
  `vpn.example.com:51820`. A syntactically valid but unusable config should fail
  with a targeted message rather than quietly proceeding or exporting a
  misleading client artifact. Where a safe mechanical fallback exists —
  substituting the current public IP for a still-sample endpoint — the script
  may apply it automatically but should still warn that a stable endpoint is the
  better final state.
- VPN profile names should describe routing scope, not just transport. A single
  generic WireGuard example blurs two materially different setups: host-only
  access and wider home-LAN routing. Prefer explicit profiles (host-only vs
  wider-LAN) so the intended `AllowedIPs`, forwarding requirements, and firewall
  expectations are visible at the template and installer level.
- Multiple local profiles need distinct local filenames. If two profiles share
  the same ignored local config paths, initializing the second silently
  overwrites the first profile's local state. Default local filenames should be
  profile-specific so users can keep both variants ready at once.
- If a VPN profile advertises DNS, the installer must actually provide a
  resolver on that tunnel address — advertising a DNS IP in the client profile
  is not enough on its own. Prefer a small split-DNS helper over teaching
  clients to browse the raw tunnel IP, and make that helper ignore any
  desktop-only `/etc/hosts` override for the same private name, or it will hand
  VPN clients a bogus `127.0.0.1` answer alongside the real tunnel IP.
- A VPN interface needs an explicit firewalld zone assignment. On firewalld-based
  systems the interface itself must be placed in a zone; otherwise some services
  silently fail because the default zone does not allow them (SMB may work while
  HTTPS and DNS do not). An installer that manages firewalld at all should manage
  both pieces: public ingress on the configured `ListenPort` and the
  interface-to-zone mapping for post-handshake traffic.

### 2026-09-02 — Caddy caches TLS certs in memory; reload does not re-read them (portfolio-general)

Up-integrated from `clockwork` and `wiring-harness`.

- Caddy reads TLS certificate files into memory on startup. `systemctl reload`
  (or `reload-or-restart`) sends SIGHUP, which does a graceful *config* reload
  but does NOT re-read cert files from disk. After rotating or replacing certs,
  always `systemctl restart caddy`.

### 2026-08-30 — Runtime masks must outrank rendered user units in live activation tests

- `systemctl --user mask --runtime` places a mask below a unit subsequently
  rendered into `~/.config/systemd/user`, so it does not reliably block the
  test workload. For a disposable user-manager test, place the `/dev/null`
  mask in `${XDG_RUNTIME_DIR}/systemd/user.control/` after activation, then
  reload the manager and assert the service remains runtime-masked.
- A masked service prevents its timer from being started. Use timers with a
  safely delayed first firing, activate and verify the exact timer set, then
  apply the higher-precedence runtime-control masks before any further test
  action or assertion.

### 2026-08-29 — Completion must reconcile every canonical backlog it satisfies

- A cross-repository implementation can complete an item owned by a different
  repository's canonical `BACKLOG.md`. Before reporting the work complete,
  update that originating backlog and its local handoff; otherwise a portfolio
  rollup reintroduces already-landed work as a new priority.
- Re-render the local portfolio index after merged work and registry changes.
  Its fail-closed generation check exposes stale owner-local catalog state
  before a misleading aggregate view is used for triage.

### 2026-08-29 — Portfolio rollups must preserve the canonical-backlog boundary

- Treat each repository's tracked `BACKLOG.md` as the source of work-item text.
  A portfolio view should derive only stable opaque identifiers and safe
  metadata from it; copying raw titles creates a second source of truth and can
  disclose private topology, repository names, or credentials.
- Resolve repository paths and visibility only through the ignored catalog and
  paired registry. A local rollup may display a public slug for a reviewed
  public entry, but private entries must remain immutable IDs. Put any
  operator-reviewed readable summaries in an ignored owner-only map.

### 2026-08-28 — "Is this branch merged?" needs PR status + `git merge-tree`, never `git cherry` or a file diff

- Two obvious tests both give false "unmerged" verdicts and inflate the
  apparent backlog of branches needing PRs:
  - `git cherry origin/main <branch>` marks every commit of a **squash-merged**
    branch as unmerged, because the squash rewrites patch-ids. A one-commit
    branch survives (its patch-id is preserved), but any multi-commit
    squash-merged branch reads as fully unmerged.
  - Comparing `<branch>:file` against `origin/main:file` marks a
    **merged-then-superseded** branch as unmerged, because `main` advanced the
    file *after* the merge. The files differ, but that is main being newer, not
    the branch having pending work.
- In this sweep those two errors turned ~0 real PR candidates into an apparent
  "24 unmerged branches." Every one, on inspection, was already merged, closed
  on purpose, already had a PR, superseded, or throwaway scratch.
- The reliable classification uses two signals:
  1. **PR status is authoritative** where it exists. A merged PR means merged;
     an open PR means already tracked; a *closed-unmerged* PR means someone
     decided not to land it — reopening is a decision, not a cleanup.
  2. **`git merge-tree --write-tree origin/main <branch>`** (git >= 2.38) does a
     real three-way merge with no working tree. Exit 0 with the merged tree
     equal to `origin/main^{tree}` means merging changes nothing — the content
     is already in main (squash leftover or behind-main), i.e. redundant. A
     non-zero exit means the merge conflicts — the branch has diverged from
     main, typically superseded work, and is not a clean PR. Only a clean,
     non-empty merge tree with no existing PR is a branch that genuinely
     warrants a new PR.
- `git merge-tree` is safe to run against a repo another session is using: it
  writes objects to the object database but never touches refs or the working
  tree.
- Tooling: `scripts/branch_inventory.py` implements this and reports clean
  counts (`needs-pr`, `diverged`, `redundant`, `open-pr`, `closed-pr`,
  `merged`). Trust its `needs-pr` count, not a raw ahead-of-main tally. Only
  `needs-pr` should ever be auto-PR'd; `redundant` is safe to delete;
  `diverged` and `closed-pr` need a human.

### 2026-08-28 — A full-history secret scan fails on intermediate branch commits, not just HEAD

- Scrubbing a flagged string from the working tree is not enough when CI runs
  `gitleaks ... --log-opts=--all`. The scan walks every commit on the branch,
  so an intermediate commit that still contains the offending content fails the
  run even though HEAD is clean and the finding no longer exists there.
- This bit a two-commit scrub: commit 1 removed a private repo name but left
  `/mnt/4tb-m2/git/…`, which trips `portfolio-git-workspace-path`; commit 2
  fixed the path. HEAD was clean, but `--all` still found the bad path in
  commit 1, and the baseline only suppresses the *original main* commit's
  fingerprint, never a new branch commit's.
- Fix: squash the branch to a single commit so no intermediate state carries
  the flagged content. `git reset --soft <merge-base>` then one commit, and
  `push --force-with-lease` (safe on your own unmerged branch; force-pushing a
  branch with an open PR updates the PR, it does not close it).
- When you scrub a machine path, remove the pattern the rule matches, not just
  the private name inside it. `portfolio-git-workspace-path` is
  `/mnt/[…]/git/` — dropping the repo name but keeping `/mnt/…/git/` still
  fails; changing `/git/` to `/data/` clears it.
- Renaming a directory referenced by a `.gitleaks.toml` allowlist path breaks
  suppression of the *historical* location. When `license/` became
  `license-server/`, the allowlist path `license-server/keys\.example\.json$`
  no longer matched the pre-rename commits, so the synthetic sample key was
  flagged by the full-history scan. Allowlist by filename, not directory —
  `(^|/)keys\.example\.json$` matches the old and new paths both. Same root
  cause as the machine-path point: a full-history scan sees every past
  location, so allowlists must cover the old one too.

### 2026-08-24 — A gate that resolves gitignored state relative to the repo root fails open inside a worktree

- `check_portfolio_privacy.sh` resolved the visibility registry as
  `${REPO_ROOT}/config/repository-visibility/*.local.json`. The registry is
  gitignored, so it exists only in the main checkout — never in a linked
  worktree. Running from a worktree found no registry, skipped the disclosure
  audit, and printed `operational-path index gate passed`. That is a pass for a
  check that never ran, and private repository names reached tracked files of a
  public repo behind it.
- This generalizes past this one script. Any gate whose inputs are gitignored —
  registries, allowlists, baselines, policy files — silently loses those inputs
  in a worktree, a fresh clone, and CI. Resolve them from the main checkout
  with `git rev-parse --path-format=absolute --git-common-dir` (its parent is
  the main checkout) rather than from the script's own location.
- Never let a skipped check print like a passed one. Where the check genuinely
  cannot run — CI and fresh clones legitimately have no registry, so it cannot
  fail closed unconditionally — say SKIPPED on stderr and state that the run
  proves nothing. Offer an env var that forces fail-closed for callers that
  want it.
- Verify a security gate from the main checkout before trusting a clean result,
  and prefer running it against the index rather than the working tree: this
  audit scans the Git index, so unstaged fixes look like failures and unstaged
  mistakes look like successes.

### 2026-08-24 — Audit every public repository for private names, not just the control plane

- The disclosure audit ran against the control plane alone, because
  `check_portfolio_privacy.sh` passed one `--root`. Every other public
  repository was unaudited, and the sweep that closed that gap found 42 tracked
  files across 10 public repos naming a private repository — including whole
  tracked directories named after private repos under `examples/`.
- A private repository naming itself is not a disclosure. The rule is a private
  name inside a **public** repository's tracked files, so the sweep must know
  each repo's visibility and skip private ones. Skip unregistered repos too:
  fail-closed means unknown visibility is not treated as public.
- Registry completeness is a precondition, not a detail. A repository absent
  from the visibility registry cannot be protected by the disclosure audit,
  because the audit has no basis to know its name is private. Reconcile the
  registry against GitHub with `repository_visibility.py audit` before trusting
  any disclosure result.
- Repository names that are ordinary English words produce noisy matches.
  Confirm a hit by looking at the reference before treating it as a leak.

### 2026-08-23 — Governance lint must be concept-based; exact-string markers manufacture false positives

- `portfolio-audit.sh` checked the AGENTS.md sudo boundary with a literal
  `grep -qF` for one repo's exact sentence. Three repos each carried a correct,
  complete Sudo Boundary section in different words and were reported as gaps
  every run.
- A governance audit that cries wolf is worse than no audit: the standing
  warnings train the reader to skim the output, and the two repos that
  genuinely had no sudo boundary sat in the same list as the three false
  positives.
- `check_security_md.py` had already been written concept-based for exactly
  this reason. The lesson did not transfer to the AGENTS.md check because it
  lived in that one script's docstring rather than here. Applying it produced
  `check_agents_md.py`.
- Check the *rule*, not the *sentence*. For the sudo boundary that means
  requiring both halves — a denial of `sudo` to the agent and a handoff of the
  exact command to the user — in any wording, since a heading alone and a bare
  mention of `sudo` both pass a naive test.
- Scope a check to the repos it can apply to. Local CI verification is only
  meaningful where CI exists, so `check_agents_md.py` gates that check on the
  presence of a workflow rather than holding docs-only repos to it.

### 2026-08-23 — A lesson duplicated verbatim in two repos belongs in the control plane

- Cross-repo duplication is the cheapest available signal that a lesson is
  portfolio-general rather than repo-specific. When the same rule is written
  out in two sibling `LESSONSLEARNED.md` files, neither repo owns it and both
  copies drift independently.
- Detect it mechanically rather than by memory: normalize every repo lesson
  (strip the bullet marker, collapse whitespace, truncate to a stable prefix)
  and look for texts appearing in more than one repo. Ignore the shared
  template header bullets, which are duplicated by design.
- Up-integrate the rule here, then leave the repo-level copy only when the
  repo genuinely narrows it. Do not delete a repo lesson that carries
  repo-specific detail the general form drops.
- `scripts/find_duplicate_lessons.py` runs this detection across the portfolio.
  Restrict it to real repositories: rendered tier overlays copy a repo's
  lessons verbatim and otherwise register as duplicates of their own source.

### 2026-08-23 — Merging to `main` in `casonk/.github` is a portfolio-wide deploy

- Calling repos reference the shared workflows as `@main`, not a semver tag,
  so a merge there takes effect on every downstream repo's next run with no
  further action. Treat those merges as a deploy with portfolio blast radius,
  not as a local change to one repository.
- The same `@main` pinning is why Dependabot does not raise `github-actions`
  PRs in calling repos: they pin no action versions of their own. The single
  Dependabot PR in `casonk/.github` is the only update needed, and every
  caller inherits it.
- After merging a reusable-workflow change, check a downstream repo's next run
  before considering the work done. A workflow that is syntactically valid can
  still break callers that pass inputs the new version no longer accepts.
- Up-integrated from `dot-github`.

### 2026-08-23 — Suppressing stderr without validating the result turns a hard failure into silent wrong output

- `cmd 2>/dev/null` discards the message that explains why a command produced
  nothing. Combined with a substitution like `x=$(cmd 2>/dev/null)`, an
  invalid flag, a missing field, or an auth failure all look identical to a
  legitimately empty result.
- This has now caused real portfolio incidents in both directions: an
  `auto-pass` lookup that returned an empty secret without saying why, and a
  `gh pr list --json <invalid-field>` call in a portfolio sweep that reported
  zero open PRs while an open PR existed.
- Either drop the redirect, or keep it and validate the result explicitly —
  check the exit status and assert the value is non-empty and well-formed
  before acting on it. Never let `2>/dev/null` be the only error handling.
- Up-integrated from `auto-pass`.

### 2026-08-23 — `stat -f` / `stat -c` fallbacks are not portability

- Writing `stat -f ... || stat -c ...` looks like a BSD/GNU fallback but is
  not one. GNU `stat -f` is valid — it queries the *filesystem*, not the file
  — so on Linux the first branch succeeds and returns the wrong data, and the
  fallback never runs. The failure is silent and platform-specific.
- Detect the platform explicitly (`uname -s`, or a capability probe whose
  failure mode is unambiguous) instead of relying on one form erroring out.
- The general rule: a `||` fallback is only safe when the left side *fails*
  on the other platform. If it succeeds with different semantics, the fallback
  is a correctness bug that testing on one OS cannot catch.
- Up-integrated from a private encrypted-store repo; related to the existing
  macOS shell compatibility lesson below.

### 2026-08-23 — Secret scanners must be told that ciphertext is not a leak, and guards must match key shape

- Encrypted payloads tracked on purpose (`payloads/*.age`, packaged bundles,
  sealed exports) trip entropy rules. Allowlist them explicitly in
  `.gitleaks.toml` rather than accepting a permanently red scan or baselining
  each new file as it appears.
- A guard that matches a key's *name* rather than its *shape* is close to
  useless: it fires on prose that mentions the key and misses the value when
  it appears under a different label. Write rules against the literal shape of
  the material (prefix, alphabet, length), not the identifier next to it.
- Allowlisting ciphertext is safe only because the plaintext boundary is
  enforced elsewhere. Keep both halves — never record a plaintext digest
  beside an encrypted payload, since the digest re-leaks what the encryption
  protects.
- Up-integrated from a private encrypted-store repo.

### 2026-08-23 — Auto-fixing pre-commit hooks exit 1 on their first run; re-run before concluding

- `ruff`, `ruff-format`, `trailing-whitespace`, and `end-of-file-fixer` modify
  files and exit non-zero on the run that made the change. That first failure
  is the hooks working, not a lint error. Re-run immediately, confirm exit 0,
  then stage what the formatter rewrote.
- Treating the first exit 1 as the verdict is how formatting reaches CI. Run
  `pre-commit run --all-files && pytest -q` before every commit, including
  docs-only ones — formatting violations turn up in `.py` files touched
  incidentally.
- Hook versions must match the versions CI pins, exactly. A stale local pin
  passes on rules the newer CI version rejects; `ruff` v0.4.4 accepted an
  import order that v0.15.9 failed, because the older pin lacked src-layout
  auto-detection. Keep `.pre-commit-config.yaml` revs in sync with
  `.github/workflows/ci.yml`.
- Where a repo must diverge from the shared config, record why in the config
  itself. The portfolio's one repo still on a Python 3.9 floor drops the
  `black` hook because black 26.x requires >= 3.10; the comment is what keeps
  the divergence from reading as drift.
- Up-integrated from `crew-chief` and `dyno-lab`.

### 2026-08-23 — Shell entrypoints need `shellcheck` in CI, not just successful local execution

- A shell script that runs correctly on the author's machine can still carry
  unquoted expansions, stale locals, and word-splitting bugs that only fire on
  unusual input. Permissive local execution proves nothing about those paths.
- Repos with shell entrypoints should run `shellcheck` in CI and validate new
  wrappers against it before pushing, rather than discovering the failure as a
  blocked push.
- zsh in particular does not word-split unquoted variables the way bash does,
  so a loop like `for r in $REPOS` silently iterates once over the whole
  string. Use an explicit array. This has recurred repeatedly in portfolio
  sweep scripts.
- Up-integrated from `shock-relay`.

### 2026-08-23 — AI assistant session transcripts are secret-bearing; never use real ones as fixtures

- Claude and Codex session files can contain copied logs, credentials,
  personal data, and private absolute paths. They are operational memory, not
  test data.
- Tests that exercise transcript parsing must use minimized synthetic
  fixtures. Do not copy a real session file into `tests/`, even after a
  manual skim — the risk is what the skim misses, and the file is then
  permanent in Git history.
- The same boundary applies to what such tooling exposes: local JSONL can
  reveal token counts, model, cache, and service-tier metadata. Sanitize
  before caching or rendering, and label anything the file cannot actually
  prove as unavailable rather than estimating it.
- Up-integrated from `session-control`; this is the tracked-file counterpart
  of the `CHATHISTORY.md` local-only rule.

### 2026-08-23 — Setup-script placeholder validation must reject realistic-looking sample values

- Checking for angle-bracket placeholders is not enough. An example that ships
  a syntactically valid but unusable value — a well-formed key that is not the
  real key, a routable-looking address that is not the real address — passes
  generic placeholder detection and then fails at runtime with a confusing
  error far from its cause.
- Validate against the *pair*, not the field. When two values must correspond
  (a private key and its matching public key), detecting that both are still
  the shipped sample is the check that matters; either one alone is
  ambiguous.
- Scan active configuration statements, not comment prose. Sample configs
  legitimately mention placeholder tokens in comments, and validating the
  whole file produces false positives that train operators to ignore it.
- Where a safe mechanical fallback exists, apply it and say so, rather than
  failing with a message the operator must interpret.
- Up-integrated from `short-circuit` and `snowbridge`, which carried this rule
  in duplicate.

### 2026-08-23 — Caddy caches TLS certificates in memory at startup

- `systemctl reload` does not re-read certificate files from disk. After
  rotating or replacing a certificate, restart Caddy — a reload leaves the
  previous certificate serving and the change appears not to have taken.
- With Caddy's admin API disabled, a correct file on disk does not prove what
  the live process is serving. Verify from a client against the real endpoint,
  not by inspecting the file.
- Rotating an mTLS leaf does not revoke its predecessor when the ingress
  trusts the issuing CA. Rotation and revocation are separate operations, and
  only the second one removes access.
- Up-integrated from `clockwork` and `wiring-harness`, which carried the
  caching rule in duplicate.

### 2026-08-10 — Gitleaks baselines are pinned to commit SHAs and do not survive a history rewrite

- A `.gitleaks-baseline.json` entry suppresses a finding by *fingerprint*, and
  the fingerprint embeds the commit SHA (`<commit>:<file>:<rule>:<line>`). Any
  history rewrite re-creates every commit under a new SHA, so every baseline
  entry silently stops matching and previously accepted findings all come back.
  The symptom is a Secret Scan that fails without any change to the repository
  contents.
- Regenerate baselines from a **clean clone of the remote**, never from a local
  checkout. A local checkout can still reach pre-rewrite commits through stale
  refs and reflogs, so `--log-opts=--all` there yields a superset — in this
  sweep, exactly double the real count.
- Before accepting a regenerated baseline, diff it against the old one on
  `(RuleID, File, StartLine, EndLine, Secret, Match)`. Identical sets prove
  only the commit anchors moved and that no new secret was quietly accepted.
  Do this unredacted and locally; `--redact` blanks the field the comparison
  depends on.
- `gitleaks/gitleaks-action@v2` has **no way to consume a baseline file**. A
  workflow that runs the action on push/pull_request and the CLI with
  `--baseline-path` only on schedule will pass or fail based on the shape of
  the push rather than the contents of the repo. Run the CLI with the baseline
  on every trigger.

### 2026-08-10 — `pre-commit run --all-files` does not see untracked files

- `--all-files` means all files *known to Git*. New files that are still
  untracked are skipped entirely, so a green local run says nothing about
  them, and they land unlinted the moment they are committed.
- `git add` new work **before** running the hooks, and re-run the hooks after
  staging. Otherwise CI is the first thing to lint the new code.

### 2026-08-10 — Rolling-window defaults plus fixed fixture dates create time-bomb tests

- When production code defaults to a rolling window (`today - 60d`) and a test
  fixture pins a fixed date, the test passes until the calendar moves past the
  window and then fails with no code change. Date fixtures that must fall
  inside a rolling window should be derived from `date.today()`.
- Tests that pass an explicit `since=` are unaffected; the trap is specifically
  tests that exercise the default.

### 2026-08-10 — Platform-specific liveness checks fail open on macOS

- `Path("/proc/<pid>").exists()` is a Linux-only liveness probe. On macOS there
  is no `/proc`, so it reports every process as dead. Where that answer gates a
  delete or prune, the failure mode is silent data loss on the developer's own
  platform while Linux CI stays green.
- Use `os.kill(pid, 0)`, which performs the existence and permission checks
  without delivering a signal. Treat `PermissionError` as live — the process
  exists but belongs to another user.

### 2026-08-10 — Tool-prefixed environment variables can collide with the tool's own config

- `rclone` maps `RCLONE_<FLAG>` environment variables onto its flags, so a
  workflow that pins a release with `RCLONE_VERSION: 1.75.0` makes every
  `rclone` invocation parse it as the boolean `--version` flag and abort.
- Name release-pinning variables so they cannot be read as flags
  (`RCLONE_RELEASE_VERSION`). The same hazard applies to any tool that adopts
  the `TOOL_FLAG` environment convention.

### 2026-08-10 — `skip-install` and `run-pytest` are contradictory in the shared Python CI workflow

- `skip-install: true` suppresses the install step that also provides pytest,
  so pairing it with `run-pytest: true` exits 127 before running a single test.
- A repo that ships scripts and config but still has tests should declare a
  build backend with an empty `py-modules` list rather than skipping the
  install. `pip install -e ".[dev]"` then succeeds without inventing a package
  layout and provides the test runner.


### 2026-07-25 — A portfolio master should be an identity-bound orchestrator, not a Git superproject

- Keep private repository names and checkout layout in ignored owner-only
  control-plane files keyed by immutable hosted repository IDs. Do not expose
  the portfolio through tracked submodules, gitlinks, or duplicate per-repo
  visibility lists.
- Gate the Git index against the active private registry before publication.
  Removing a private name from the current tree does not remove it from Git
  history; any history rewrite is a separate destructive review with backup
  and collaborator coordination.
- Compare the complete hosted inventory with expected checkout paths. Scanning
  only repositories already present locally hides missing clones, while
  scanning only the hosted registry hides local-only repositories.
- Materialization should clone missing repositories atomically and
  synchronization should fetch only clean, identity-matched checkouts. Never
  make `pull`, reset, clean, stash, push, visibility, archive, or deletion part
  of an unattended portfolio sync.
- Repository retirement is an evidence-backed saga: map reverse dependencies,
  restrict visibility if reviewed, archive, cool down, verify backup, and only
  then consider a separately approved manual deletion. GitHub and Git effects
  remain outside any local ACID transaction even after the authority moves to
  a quorum-backed database.

### 2026-07-20 — Offline user-unit verification still needs a runtime directory

- `systemd-analyze --user verify` builds an in-process user-manager model even
  when no live manager is contacted. On systemd 252 it fails before parsing
  units if `XDG_RUNTIME_DIR` is absent.
- In non-booted test containers, create a private mode-0700 runtime directory
  only for the verification command, unset `DBUS_SESSION_BUS_ADDRESS`, retain
  `--user`, and assert that no user bus appears. Do not export the runtime path
  into later activation-refusal checks.
- Disable generators and man-page lookup for deterministic isolated parsing,
  and use `--recursive-errors=yes` so dependency errors affect the exit status.
  `--offline=` is for `systemd-analyze security` in systemd 252, not `verify`.

### 2026-07-19 — Treat Podman machine state and connection selection explicitly

- Inspect an existing macOS Podman machine before starting it. Starting an
  already running machine is an error, and rootful or transitional machines
  should be refused instead of silently repurposed.
- Pass `--update-connection=false` during machine initialization and startup,
  then verify through `podman --connection <name>` so automation does not
  replace an operator's default connection or accidentally test another
  runtime.
- Podman upstream recommends its signed macOS installer; a Homebrew path is
  still useful for unattended repository bootstrap when it is disclosed.
  Gate that path to the formula's current Apple Silicon/macOS 13+ support
  floor. Also disclose that Podman machine's default macOS configuration
  exposes the login user's home directory to the VM.

### 2026-07-19 — Ordinary containers validate rendering, not user-manager activation

- A networkless Linux container is a strong boundary for support-repo cloning,
  exact profile selection, real Clockwork rendering, unit syntax validation,
  and idempotent reruns.
- Do not treat a successful fake `systemctl` call as live activation coverage.
  The installer intentionally refuses `--activate` when no systemd user
  manager is reachable, and that refusal should happen before state or live
  unit files are written.
- Real activation tests need a disposable systemd-booted environment with a
  dedicated non-root user manager. Runtime-mask every managed service before
  enabling timers so validation cannot launch provider-backed workloads.

### 2026-08-16 — Scheduler identity belongs in the shared renderer contract

- When a downstream installer and Clockwork both manage one launchd workload,
  give Clockwork the downstream's explicit label and use Clockwork as the sole
  plist renderer. The web UI, installer, status, and controls must resolve that
  same label or duplicate schedules can be loaded under different namespaces.
- Preserve systemd-only semantics visibly: fixed login delay, jitter, and
  network readiness belong in the reviewed downstream adapter, while direct
  Clockwork rendering of `after`, `wants`, `on_boot_sec`, or randomized delay
  remains fail-closed.
- Launchd `StartInterval` is not a persistent interval timer for sleeping
  laptops. Use a short truthful poll plus locked owner-only state: derive the
  first due time from the actual boot time, keep failed work due behind a
  bounded retry, and advance the real interval only after success. Apply
  jitter and readiness gates only after the due decision.
- Never reparse private environment files in a downstream shell adapter when
  the shared renderer already owns a no-follow, owner-only data loader. When
  that loader wraps `ProgramArguments`, scheduler controls must validate the
  exact inner argv rather than rejecting or broadly trusting Python wrappers.
- A multi-job activation must render and validate the complete candidate set
  before unloading anything. Snapshot every managed artifact and scheduler
  state first, then roll the whole transition back if the nth unload, install,
  enable, or bootstrap fails.
- Launchd's disabled override map has three observable states: absent, explicit
  `false`, and explicit `true`. Do not collapse absent and `false` into one
  boolean during a transaction. Avoid `launchctl enable` unless changing an
  explicit `true`; rollback only that mutation and re-query the complete map so
  exact restoration remains provable without an unsupported remove-override
  operation.

### 2026-07-19 — macOS shell compatibility needs runtime tests, not syntax checks alone

- Stock macOS `/bin/bash` is still Bash 3.2. A script can pass `bash -n` while
  failing at runtime on unsupported commands such as `mapfile`, or while
  expanding an explicitly empty array under `set -u`.
- Execute scheduler-facing shell workloads with `/bin/bash` against both an
  empty portfolio and a representative repository fixture. Keep indexed-array
  loops safe when discovery returns no repositories.
- Do not assume GNU utility behavior on macOS. Use portable syntax where
  possible and explicit platform branches for differences such as GNU
  `du --exclude` versus BSD `du -I`.

### 2026-06-29 — Check Android TV network placement before diagnosing ADB

- A Sharp Android TV 10 device with USB debugging enabled exposed authenticated
  ADB on TCP port 5555 only after it joined the primary Wi-Fi network. Guest
  isolation had made the port appear unavailable from the management host.
- Before concluding that an Android TV firmware lacks network ADB, verify both
  devices are on the intended non-isolated network, confirm the TV's current
  per-SSID MAC and lease, then retest the standard ADB port.
- On this firmware, ADB could revoke AirScreen location permission and deny
  overlay access while preserving AirPlay, but Android blocked shell-level
  disabling of the app's boot receiver. Keep boot/background startup as a
  manual in-app control.
- For `io.github.jqssun.airplay`, `AirPlayService` is deliberately
  `exported=false`, so shell `am startservice` and `am stopservice` are not a
  supported control API. A reliable ADB wrapper can keep launch-time auto-start
  enabled while boot start remains disabled: launch the package to start, use
  `am force-stop` to stop, and inspect `dumpsys` plus DNS-SD for status.

### 2026-06-28 — Do not use `path` as a loop variable in zsh

- In zsh, `path` is a special array tied to the `PATH` environment variable.
  Assigning a filesystem path to a loop variable named `path` can replace
  command lookup and make subsequent commands fail with `command not found`.
- Use names such as `target_path` in zsh verification and cleanup scripts.

### 2026-06-21 — Pre-commit all-files does not validate untracked generated artifacts

- `pre-commit run --all-files` only runs against tracked files. If a change adds
  new generated artifacts, validate them with `pre-commit run --files <paths>`
  before commit, or stage them first and rerun the full gate.
- Otherwise CI can fail on fresh checkout for basic hooks such as
  `end-of-file-fixer` even though local all-files validation appeared clean
  before the new files were tracked.

### 2026-06-21 — New systemd-managed repo scripts need an explicit executability check before install

- If a user service `ExecStart=` points straight at a repo script, confirm the
  script has the executable bit before treating the unit as ready.
- Otherwise systemd fails with `status=203/EXEC` and `Permission denied` even
  though the file path and shebang are correct.
- For traction-control installer work, add `bash -n` plus an `ls -l` or mode
  check for any newly introduced script, then run one live no-op start when the
  service is intended to be operator-triggered.

### 2026-06-18 — WebTerm launchers must agree on the base tmux session

Session launchers such as `session-control` and WebTerm must target the same
base tmux session. If a web launcher appears to open only blank shells, first
compare the launcher config (`SESSION_CONTROL_TMUX_SESSION`), the WebTerm local
config (`WEBTERM_TMUX_SESSION`), and the live `ttyd.service` /
`pit-box-api.service` `ExecStart` lines. A stale WebTerm unit can attach browser
tabs and API state to the old base session while the launcher correctly created
the intended resume window in the new one.

Keep `WEBTERM_TMUX_SESSION` explicit in the owning repo's local `settings.env`,
regenerate the ignored build units, and apply them with the repo's sudo rebuild
command instead of changing provider resume-command generation.

### 2026-06-18 — Claude CLI produces no stdout in `$()` subshell inside systemd user services

In a systemd user service session (no controlling terminal), the Claude CLI
exits 0 but writes nothing to stdout when its output is captured in a `$()`
subshell. The same invocation works in an interactive shell and via direct file
redirect (`> file`). This caused every scheduled agentic run to silently no-op.

**Fix:** Always write agent output directly to a file instead of capturing
with `OUTPUT="$(run_claude 2>&1)"`. Use:

```bash
run_claude > "${AGENT_OUTPUT_FILE}" 2>&1
AGENT_STATUS=$?
cat "${AGENT_OUTPUT_FILE}" >> "${LOG_FILE}"
```

This applies to all three providers (codex, claude, copilot) in any agentic
script that runs as a systemd oneshot service. The readiness probe using
`claude auth status` works fine (local operation, no API call). The broken
pattern will always show `agent exit status: 0` with an empty agent-output.txt
(1 byte, just a newline from `printf '%s\n' ""`).

### 2026-06-20 — Discovery-only maintenance modes must not depend on repair-provider readiness

- When a maintenance script supports a read-only discovery-only mode, skip any
  provider readiness probes that exist only to launch a later repair or
  authoring agent.
- Otherwise discovery can fail for unrelated model-auth, quota, or
  provider-session reasons even though the read-only GitHub/API inventory path
  is healthy.
- For split agentic designs, treat discovery auth and repair auth as separate
  contracts and keep the discovery half runnable on its own.

### 2026-06-20 — Provider-selection logs must not share stdout with command-substitution results

- If a helper like `agentic_resolve_provider` returns a provider name through
  stdout and is called inside `$(...)`, redirect any readiness or status logs
  to stderr first.
- Otherwise human-readable readiness logs contaminate the captured provider
  string and downstream logic sees a malformed provider value.
- This applies to shared provider-selection helpers used by unattended systemd
  jobs, where stdout is often both machine-consumed and journal-captured.

### 2026-06-18 — Do not run ad hoc `/tmp` scripts that mutate shared Caddy or dnsmasq state

Temporary scripts that append directly to `/etc/caddy/Caddyfile`,
`/etc/dnsmasq.d/*.conf`, or `/etc/hosts` bypass the portfolio service registry
and can break unrelated services. A crew-chief bench setup script did this by
adding a Caddy site and reloading Caddy without preserving
`import Caddyfile.d/*.caddy`, which made WebTerm return an empty `200` because
its repo-owned drop-in was ignored.

New internal web surfaces should be added through `wiring-harness` registry
entries or through the owning repo's checked-in install/rebuild scripts, with
the generated config reviewed before reload. For emergency recovery, prefer a
temporary Caddy admin API load that restores the missing import, then apply the
persistent fix through the owning repo's sudo rebuild command.

### 2026-06-18 — KeePass profile names, entry paths, and local machine paths must never appear in tracked example files or docs

The pattern `profile = infra` (or any real vault name: `personal`, `finance`, `master`, `work`) in tracked config/example files is a security incident. Similarly, hardcoded local machine paths (`/mnt/4tb-m2/git/`, device hostnames like `bully` in mount paths) and real KeePass entry paths must not appear in README, AGENTS.md, blueprint, LESSONSLEARNED, or example files.

**Correct conventions:**
- Example config files: `profile = your-profile`, `keepass_entry = your-service/component/purpose`
- Python source defaults: `DEFAULT_KEEPASS_PROFILE = ""` (empty string, require explicit config)
- Machine paths in docs or lessons: `$VARIABLE`, `<placeholder>`, or `relative/path`
- Snowbridge mount paths: `/mnt/snowbridge/receipts` not `/mnt/setup/<device>/...`
- Private application workspace: `$REPO_ROOT` or
  `$(git rev-parse --show-toplevel)`, never a tracked machine-specific path

**Enforcement:** The `keepass-real-profile-name` gitleaks rule (added 2026-06-18 across all portfolio repos) catches `profile = (infra|personal|finance|master)` in non-doc files. The `portfolio-git-workspace-path` rule catches absolute local paths. Both rules have baselines for historical commits that can't be rewritten. Run `gitleaks git --config .gitleaks.toml --baseline-path .gitleaks-baseline.json` before publishing any repo as public.

### 2026-06-18 — LESSONSLEARNED.md command examples must use env vars or placeholders for machine-specific paths

When a lesson includes a command invocation, never paste the literal local path. Use `$VARIABLE` or `<path-to-repo>` instead. Example: `python3 "$SHOCK_RELAY_ROOT/services/gmail-imap/send_email.py" <to> <subject> <body>` with a note that `SHOCK_RELAY_ROOT` is the local sibling path. This makes lessons portable and safe to leave in tracked public files.

### 2026-06-18 — Baseline generation: always use `gitleaks git -r /tmp/file.json && cp /tmp/file.json .gitleaks-baseline.json`

Writing `.gitleaks-baseline.json` manually produces fingerprint mismatches. Always generate it by running `gitleaks git --config .gitleaks.toml --no-banner -r /tmp/repo-baseline.json` (which exits 1 on leaks but still writes the file), then copy the output directly to `.gitleaks-baseline.json`. Verify clean with `gitleaks git --baseline-path .gitleaks-baseline.json`. Never hand-craft the JSON.

### 2026-06-17 — Never record literal secret values in durable lessons or command examples

Durable lessons may describe where a secret is stored, which entry or attribute
name to read, and which command pattern to use, but they must never include the
secret value itself. Use placeholders such as `<passphrase-from-keepass>` in
tracked docs, examples, commit messages, PR bodies, and handoff notes. If a
literal credential, token, private path, or private identifier lands in tracked
history, treat it as a secret incident: remove it from the current tree, rewrite
published history, force-push the sanitized refs, and rotate the affected
secret.

### 2026-06-17 — Reusable workflows in `casonk/.github`; git commit email must be the GitHub no-reply address

All portfolio CI workflow action version pins belong exclusively in `casonk/.github` (locally `./util-repos/dot-github`). Calling repos reference them as `uses: casonk/.github/.github/workflows/<name>.yml@main` — no action pins in the caller. A single Dependabot PR on `casonk/.github` propagates to all repos automatically. Because callers use `@main` (not a semver pin), Dependabot on individual repos generates no `github-actions` PRs — no conflict.

When committing to a newly-initialized repo that will be pushed to GitHub, set `user.email` to the GitHub no-reply address (`<userid>+<username>@users.noreply.github.com`) before the first commit. GitHub blocks pushes from private email addresses; configure the private email only in local git config, never in tracked files or commits on repos with push protection enabled.

### 2026-06-17 — Never install third-party packages without explicit user confirmation; prefer existing portfolio tooling

During a security triage session, `pykeepass` (a third-party library that opens `.kdbx` files directly) was installed via `pip install pykeepass --user` to write a SHA-256 hash as a custom KeePass attribute — without first asking the user. This violated two rules:

1. **Confirmation gate**: installing a new package is a trust decision (supply chain, scope, persistence). Always ask the user before running any `pip install`, `npm install`, `brew install`, `apt install`, or equivalent.
2. **Portfolio tooling preference**: the `auto-pass` shared utility (`./util-repos/auto-pass`) exposes `upsert_keepassxc_entry(entry, username, password)`, which uses `keepassxc-cli` to create or update a dedicated KeePass entry. A new entry with the hash stored as the password field is the correct, standard pattern — no third-party library needed.

**Correct pattern for storing a credential-derived value** (e.g., a Guacamole SHA-256 hash):
- Create a dedicated KeePass entry: e.g., `service-name/component/hash-entry`
- Store the hash as the `password` field via `upsert_keepassxc_entry`
- Read it back with `resolve_keepassxc_entry` and `attrs_map={"hash": "password"}`

**Why `keepassxc-cli` lacks `--custom-attribute` on `add`/`edit`** (as of v2.7.12): this was the root cause of the pykeepass detour. When a CLI doesn't support a needed flag, look for a creative fit within existing commands before installing a new library.

**How to apply**: Before any `pip install` (or equivalent), explicitly state what the package does and ask for confirmation. Prefer auto-pass, keepassxc-cli, or other existing portfolio tooling over new package installs for credential and KeePass operations.

### 2026-06-16 — Loading an SSH key passphrase non-interactively from KeePass; use `sshpass` + `ssh-add`

To load `~/.ssh/id_ed25519` non-interactively, retrieve the passphrase from the
relevant KeePass entry and pass it via `sshpass`:
```bash
eval "$(ssh-agent -s)"
sshpass -P "passphrase" -p "<passphrase-from-keepass>" ssh-add ~/.ssh/id_ed25519
```
Store the passphrase as a custom attribute in the relevant KeePass entry (the KeePassXC SSH
agent integration reads it from the Notes SSH section as `passphrase: <attribute-name>`).
The `SSH_ASKPASS` / `DISPLAY` trick does not work headlessly; `sshpass -P passphrase`
is the reliable path. Each Bash tool call runs in a fresh shell, so all steps
(agent start, key add, git push) must be chained with `&&` in a single command.

### 2026-06-14 — Wiring-harness Caddy regeneration can orphan repo-owned drop-ins

When `wiring-harness/scripts/setup_caddy.py --provision` rewrites
`/etc/caddy/Caddyfile`, it can remove the `import Caddyfile.d/*.caddy` line that
repo-owned Caddy sites such as `pit-box-webterm` depend on. The result is a
hostname that still resolves and still has a backend running, but Caddy serves
an empty default response because the repo-owned drop-in is ignored.

For any service with `ingress = "repo-caddy"`, verify both the drop-in file and
the main Caddyfile import are active after wiring-harness regeneration. If a
non-sudo emergency restore is needed, adapt a temporary Caddyfile that imports
`/etc/caddy/Caddyfile.d/*.caddy` and POST it to Caddy's localhost admin API,
then hand off `sudo ./scripts/rebuild_webservices.sh caddy` from the owning repo
for the persistent `/etc/caddy/Caddyfile` fix.

### 2026-06-13 — dnsmasq bind-interfaces and bind-dynamic are mutually exclusive across drop-in files

When multiple repos (short-circuit, pit-box) each install a dnsmasq drop-in config to
`/etc/dnsmasq.d/`, a `bind-interfaces` in one file and `bind-dynamic` in another causes
dnsmasq to fail silently at boot with "cannot set --bind-interfaces and --bind-dynamic".
The failure kills DNS resolution for all WireGuard peers, making all `.internal` web services
unreachable even though Caddy and backends are running fine.

Always use `bind-dynamic` for WireGuard-facing dnsmasq configs — it handles the interface
coming up after dnsmasq starts and is compatible with multi-interface setups. Run
`dnsmasq --test` after writing any new dnsmasq config to catch conflicts immediately.
Add a systemd `OnFailure=` drop-in to dnsmasq so boot-time failures trigger an email
rather than requiring manual triage.

### 2026-06-11 — Stale-file rotation must target ignored generated state, not tracked history

When reviewing old files for archive rotation, classify candidates by ownership
before age or size. Good automation targets are ignored generated outputs,
runtime caches, debug snapshots, temp downloads, reports, and browser profiles
with a verified restore path. Do not move tracked coursework, certifications,
source docs, credentials, raw private/account data, or irreplaceable inputs into
generic archive rotation just because they are old. Prefer repo-local reversible
tarball archives with manifests, retention limits, and restore commands; add
post-run hooks when stale cleanup should happen by age, and keep tachometer
disk-pressure automation for pressure-triggered remediation.

### 2026-06-11 — End each meaningful session with a lesson-capture gate

Do not rely on memory to decide whether a reusable lesson should be written.
Before final reporting, explicitly classify the session outcome: if the work
revealed a reusable rule, failure mode, workflow pattern, provider quirk,
verification condition, or safety boundary, update the appropriate
`LESSONSLEARNED.md`; otherwise say no durable lesson was added and why. This
prevents durable guidance from being buried only in `CHATHISTORY.md` or
conversation.

### 2026-06-09 — Never silently drop UI functionality when refactoring layout

When restructuring HTML layout (splitting rows, merging rows, changing toolbar structure), audit every button and link in the original against the new version before writing the file. Do not remove any interactive element without explicit user confirmation — even if it looks redundant (e.g., a `home` link that also exists in the topbar). Silently dropping functionality forces the user to catch regressions themselves and wastes round-trips.

### 2026-06-02 — GNOME auto-suspend can drop all services when UPower cannot detect a power supply

- On a desktop with no battery or UPS, UPower may report `battery-missing-symbolic` and `unknown` power state.
- When this happens, GNOME `gsd-power` defaults to the battery idle policy (`sleep-inactive-battery-type`), which was configured to `suspend` after 900 seconds (15 minutes) of session idle.
- The resulting `systemd-logind.Suspend()` call drops the ethernet link entirely (`enp5s0: Link is Down`), taking down Caddy, Samba, all Flask web services, and WireGuard tunnels.
- Symptom pattern: "services go offline ~30 minutes after boot" = ~15 min active use + ~15 min GNOME idle timer firing on battery policy.

**Two separate dconf databases must both be fixed:**

1. **User session (gsettings):** `gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'` and `sleep-inactive-battery-timeout 0`. Set both AC and battery variants. This persists via dconf and takes effect immediately for the logged-in user.
2. **GDM login screen (system dconf):** The GDM greeter runs as the `gdm` user with its own dconf database at `/etc/dconf/db/gdm.d/`. It does NOT read the desktop user's gsettings. Use `fedora-debugg/scripts/install_gdm_no_auto_suspend.sh` to install a locked GDM dconf policy, then run `sudo dconf update`. Requires a reboot or GDM restart to take effect.

Fixing only the user gsettings is insufficient — the machine will still suspend when the login screen is shown (e.g., after a fresh boot before login, or after screen lock).

**Do not use `systemd-inhibit --mode=block` from a system service:** polkit denies `org.freedesktop.login1.inhibit-block-sleep` to non-interactive system services even when running as root. The service loops with exit status 1 until systemd's restart rate-limit disables it. The dconf fix is the correct and sufficient solution.

### 2026-05-17 — Gitleaks Action v2 ignores unsupported `args` input

- `gitleaks/gitleaks-action@v2` does not declare an `args` input. GitHub Actions logs this as `Unexpected input(s) 'args'`, then runs the action's default event behavior.
- For scheduled full-history scans that need `.gitleaks-baseline.json`, run the Gitleaks CLI directly with `--baseline-path` instead of trying to pass CLI flags through the action.
- Keep push/PR scans on the action's event-aware defaults only when the workflow does not need custom CLI flags.
- CI-repair discovery must include scheduled workflow runs, not only `push` runs, because weekly full-history scans can fail after the latest push run has already passed.

### 2026-05-10 — Tachometer disk pressure should trigger reversible repo archive automation

- Treat open tachometer `system.disk` / `host.disk` backlog entries and summary disk utilization above threshold as automation triggers, not just dashboard warnings.
- The control-plane response is `scripts/tachometer_disk_pressure_agentic.sh`: inventory tachometer backlog/summary files, skip dirty repos by default, and only launch an agent for clean candidate repos.
- The agent should implement or repair reversible repo-local compression/decompression for local-only caches, generated artifacts, temporary downloads, and debug snapshots. Do not delete source data, credentials, raw private inputs, or irreplaceable user data as the default disk-pressure response.

### 2026-05-06 — Agents must hand off sudo commands to humans

- Agents in this portfolio cannot complete interactive `sudo` prompts. Treat any sudo-required step as a human handoff, not as an agent-executable action.
- When work requires elevated system changes, finish and validate the non-sudo repo changes first, then give the user the exact `sudo` command(s) to run and ask them to share the output.
- Do not repeatedly retry `sudo -n` after it reports that a password is required, and do not report a live sudo-backed deployment as complete until the user has run it.

### 2026-05-03 — .gitleaks-baseline.json must be excluded from the portfolio-git-workspace-path rule

- The `.gitleaks-baseline.json` file records known findings with their `Match` and `Secret` values verbatim, which include the local machine git workspace path.
- When the baseline file is committed and pushed, the push-triggered gitleaks CI scan finds those path values in the new file and fails.
- Fix: add `'''\.gitleaks-baseline\.json$'''` to the `[rules.allowlist]` paths for `portfolio-git-workspace-path` in `.gitleaks.toml`. The baseline is an internal gitleaks artifact; scanning it for violations is self-defeating.
- When deploying the secret-scan guardrails with `deploy-secret-scan.sh`, always confirm the baseline file is excluded from scanning before pushing.

### 2026-05-03 — CI repair agent must use claude, not codex

- The `ci-repair-agentic.service` used `CI_REPAIR_AGENTIC_PROVIDER=auto`, which selected codex first. Codex passed the readiness probe but produced no output and made no fixes (exit 0, empty output).
- Changed the service to `CI_REPAIR_AGENTIC_PROVIDER=claude` so the repair agent uses the Claude CLI, which reliably works for this cross-repo repair workflow.
- When reviewing the agent logs, check `~/.local/share/ci-repair-agentic/latest-output.txt` for empty output as a signal the wrong provider was selected.

### 2026-05-01 — gitleaks allowlist regex matches against the captured match text, not the full line

- When a gitleaks rule captures a short regex (e.g., `target\s*=\s*"\+1`), the global `[allowlist]` regexes are tested against that captured text, not the full file line.
- An allowlist entry like `\+1[Xx]{5,}` cannot suppress a rule whose regex only captures `+1` followed by a digit before the allowlist is even tested.
- Fix: make the detection regex itself specific enough that it never matches the known-safe pattern (e.g., change to full E.164 `\+1[2-9]\d{9}` so `+1XXXXXXXXXX` doesn't match in the first place).

### 2026-05-01 — `pre-commit run --files` crashes when called from a different repo's context

- Running `pre-commit run --files /path/to/other-repo/file` from inside repo A crashes `check-added-large-files` with a `git check-attr` non-zero exit (exit 128) because git tries to resolve the path relative to repo A, not the file's actual repo.
- Deploy scripts that call `pre-commit run --files <target-repo-file>` while cwd is the control-plane repo will always hit this error.
- The WARN+continue fallback in the deploy script is correct behavior here. If strict validation is needed, change to `git -C <target-repo> diff --cached | pre-commit run --stdin-filename ...` or just skip the pre-commit sanity check step entirely in cross-repo deploy scripts.

### 2026-05-01 — Tachometer [notify] fields contain PII and must never be tracked

- Every repo's `config/tachometer/profile.toml` had a `[notify]` section with a personal phone number (`target`) and a local filesystem path (`shock_relay_root`) committed in tracked history across 26 repos.
- These fields are machine-local personal contact info — treat them exactly like `REFS-LOCAL.md`: gitignore them or leave them empty in the tracked file.
- The safe tracked state: `shock_relay_root = ""` and `target = ""` with a comment directing contributors to set them via local config. Never seed these with real values in the shared template.
- Remediation required `git-filter-repo --replace-text` (for blobs) AND `git-filter-repo --replace-message` (for commit messages) applied separately — `--replace-text` does not touch commit message bodies. Run both together in a single invocation to avoid partial scrubs: `git-filter-repo --replace-text <file> --replace-message <file> --force`.
- For mirror/bare clones, filter-repo does not update `refs/heads/*` correctly (known limitation) — always do history rewrites on a regular (non-mirror, non-bare) clone, then force-push.

### 2026-04-29 — Fine-grained GitHub PATs need explicit repo grants and the correct auth username in git URLs

- Fine-grained PATs only cover the specific repos listed when the token was created; a token that excludes a private sibling repo will return 404 at `pip install` time even within the same account.
- When using a fine-grained PAT in a git URL, the username **must** be `x-access-token`, not `${{ github.actor }}`; classic PATs work with either.
- If a fine-grained PAT is later updated to cover "all repositories" (or to explicitly add the missing repos), switch CI secrets to it as the canonical agent token rather than keeping a classic PAT as a workaround.
- A fine-grained PAT (all repos, code read + workflows write) is the correct long-term token for portfolio CI secrets; a classic PAT with `repo` scope is the fallback. Store each token in a dedicated KeePass entry and reference it by name in CI secrets.

### 2026-04-29 — pylint matrix must not exceed windshield's Python floor

- If a pylint workflow matrix includes Python versions below `3.10` but the repo installs windshield as a dependency (which declares `requires-python = ">=3.10"`), pip will refuse the install on older interpreters.
- Either narrow the pylint matrix to only versions that meet the highest `requires-python` floor among all dependencies, or use `--ignore-requires-python` only when the version mismatch is intentional and understood.

### 2026-04-21 — Expand consent categories when AI/model or health-adjacent workflows become first-class

- A broad personal-tools consent statement is not precise enough once the portfolio grows into explicit model-mediated workflows or health-adjacent research datasets.
- Add category-level consent documents for those new domains in `./doc-repos/my-consent` and then tighten the neighboring financial, messaging, and secret wording to match the actual integrations.
- Keep the split category-based rather than repo-specific unless a provider or regulatory surface has its own materially different opt-in requirement.

### 2026-04-22 — Large portfolio CI sweeps must account for isolated checkouts and minimum Python versions

- A local workspace with sibling utility repos can hide CI failures when tests accidentally rely on those siblings existing next to the repo under test; browser/credential fallback tests should stub shared-repo lookups instead of depending on `./util-repos/*` being present in GitHub Actions.
- When a repo still lints or tests against Python `3.10` or `3.11`, avoid writing syntax that only parses on newer local interpreters such as Python `3.12+`; nested f-string quote reuse can pass locally and still fail hosted lint.
- During post-push CI repair, compare hosted failures against local environment assumptions first before assuming the underlying feature logic is broken.

### 2026-04-19 — Scheduled bug sweeps should stay review-first and target clean code repos

- A daily portfolio-wide bug-scan should inventory candidate repos from tracked source files first, so documentation-only repos are not treated as code-review targets by accident.
- Default unattended bug hunting to review-only output and logs; a generic daily sweep should not silently rewrite code unless a fix is extremely small, high-confidence, and locally verifiable.
- Skip dirty worktrees by default during bug sweeps so potential findings are not mixed with in-progress local edits.

### 2026-04-19 — Scheduled CI repair should inventory current failures before launching an agent

- An unattended CI-fix job should first identify which repos actually have a failing latest default-branch push run; otherwise the agent wastes time sweeping healthy repos and may broaden scope without evidence.
- Skip dirty worktrees by default during scheduled CI repair so the automation does not mix emergency CI remediation with in-progress local edits.
- When one repo carries multiple unattended agentic jobs, stagger their boot delays so they do not routinely start at the same moment after login or downtime recovery.

### 2026-04-19 — Scheduled agentic maintenance should preflight dirty target files and use an explicit provider order

- Unattended cross-repo agent runs should skip when the tracked files they are meant to review are already dirty; otherwise an automatic doc-consolidation pass can trample in-progress manual edits.
- When several agent CLIs are available, scheduled automation should use an explicit preference order and a fixed non-interactive mode instead of assuming an interactive operator will choose at runtime.
- When the selected model matters, do a cheap CLI readiness probe for that exact provider/model pair before launching the full maintenance prompt so quota or overload problems are discovered during provider selection instead of halfway through the real job.
- Keep unattended agent credentials and local profile overrides in local-only `EnvironmentFile` paths rather than tracked manifests; tracked defaults should carry provider/model choice, not live secrets.

### 2026-04-19 — Promote repeated example-scrubbing and admin-surface guidance into shared templates

- When several repos independently add the same warning about public `.example` files, synthetic placeholders, or local-only `CHATHISTORY.md`, move that language into the shared `SECURITY.md` and `LESSONSLEARNED.md` templates instead of leaving it scattered across repo-local files.
- Shared templates should also carry the conditional baseline that local dashboards/admin surfaces default to loopback and that wider exposure is an explicit trust-boundary decision, not just a README assumption.

### 2026-04-19 — Document intentional trust-boundary assumptions and break-glass TLS bypasses explicitly

- Generic `SECURITY.md` text is not enough when a repo intentionally listens on a LAN interface or exposes an insecure TLS override for troubleshooting.
- If a service is meant for a trusted LAN/VPN only, say that directly in `SECURITY.md` and point at the concrete bind setting that widens exposure.
- If a repo exposes `verify_tls: false` or `*.tls.insecure_skip_verify`, document those flags as short-lived break-glass settings and tell operators to prefer CA configuration over disabling verification.

### 2026-04-19 — Admin UIs and dashboards must default to loopback unless auth is built in

- If a repo expects Caddy, mTLS, or WireGuard to provide the trust boundary, keep the app's standalone default on `127.0.0.1` rather than `0.0.0.0`.
- Do not rely on deployment docs alone to protect state-changing POST routes; either require auth/CSRF in the app or make wider network exposure an explicit opt-in.
- During portfolio security audits, compare code bind defaults against example manifests and README deployment guidance to catch drift between the documented boundary and the actual runtime default.

### 2026-04-19 — Seed new repo SECURITY.md files from a shared template, then specialize

- A concept-based linter catches missing policy coverage, but it does not prevent copy/paste drift by itself.
- New repos should start from a shared `SECURITY.md` template that already covers private reporting, public-disclosure avoidance, and sensitive-content examples.
- After seeding from the template, add repo-specific boundaries such as localhost-only admin UIs, private datasets, wallet data, or infrastructure topology in the same scaffold step.

### 2026-04-11 — SECURITY.md policy lint should be concept-based and extend the existing scheduled audit

- Portfolio `SECURITY.md` files already vary by repo, so content lint should check for concepts like private reporting, public-disclosure avoidance, and sensitive-content guidance instead of enforcing one exact heading template.
- The right integration point is the existing `traction-control` scheduled governance audit, not a separate repo, when the scope is portfolio-local documentation policy enforcement.
- Repo-name copy/paste mistakes inside security-reporting text are common enough to check automatically and cheap enough to include in the daily audit.

### 2026-04-11 — Centralize repeated personal-data consent by category in `my-consent`

- When several sibling repos process the same kind of personal data, prefer one category-level consent statement in `./doc-repos/my-consent` instead of cloning near-identical repo-specific wording.
- Add repo-local links back to the matching consent document from the affected repos so the consent is discoverable where the processing happens.
- Reserve repo-unique consent files for materially different surfaces, such as provider-specific opt-in requirements or a distinctly different data category.

### 2026-04-09 — Stage a brand-new repo before trusting `pre-commit run --all-files`

- In a freshly initialized repository, `pre-commit run --all-files` only scans tracked files, so a fully untracked scaffold can misleadingly report that every hook was skipped.
- After creating a new repo baseline, stage the tracked files before treating the pre-commit result as a real validation signal.
- Keep intentionally local files such as `CHATHISTORY.md` and `REFS-LOCAL.md` gitignored while staging the tracked baseline for verification.

### 2026-04-07 — Always append exactly one final newline when inserting content at the end of a file

- The `end-of-file-fixer` pre-commit hook requires every tracked file to end with exactly one `\n`.
- Scripted file appends that call `rstrip()` before writing can leave double trailing newlines (`\n\n`) if the written block itself ends with a blank line.
- Before committing any scripted file edit, verify with `od -c <file> | tail -2` that the file ends with exactly `.\n` and not `.\n\n`.
- In Python: `content.rstrip(b'\n\r\t ') + b'\n'` is the safe normalization pattern.

### 2026-04-07 — Pin ruff and black versions consistently between local dev, pre-commit, and CI

- Different ruff versions can disagree on import sort order (I001) and formatting choices, causing local-clean code to fail CI.
- When the portfolio standardizes on a new ruff or black version, update the CI `pip install ruff==X.Y.Z black==X.Y.Z` pins and the `.pre-commit-config.yaml` `rev: vX.Y.Z` entries in the same change.
- Always validate locally with the same ruff version that CI uses: `pip install ruff==0.15.9` before running `ruff check .` and `ruff format --check .` before any push.
- After any bulk pre-commit rev bump, scan all repo CI workflows for hard-pinned formatter versions and update them in the same change. Repos that install formatters only via pre-commit are safe; only repos with explicit `pip install ruff` steps need the pin update.

### 2026-04-07 — Run pre-commit locally before every push; repo AGENTS.md specifies exact commands

- Every repo's `AGENTS.md` now contains a "Local CI Verification" section with the exact commands to run before pushing.
- The portfolio-wide rule (traction-control AGENTS.md rule 9) is non-negotiable: do not push code that has not passed local verification.
- For Python repos: `pre-commit run --all-files` then `pytest -q`.
- For docs/ops repos: `pre-commit run --all-files` is the full gate.

### 2026-04-06 — Every repo carries REFS-PUBLIC.md (tracked) and REFS-LOCAL.md (gitignored)

- `REFS-PUBLIC.md` is tracked and documents external public repositories, datasets, APIs, and documentation that the repo depends on or references. Keep it free of private or machine-specific detail.
- `REFS-LOCAL.md` is gitignored and holds hard-coded local filesystem paths to reference files, sibling repos, or local data sources needed to operate the repo on this machine.
- Both files are seeded from `./util-repos/traction-control/docs/templates/` and must be present in every repo from day one.
- When adding or updating a dependency (public or local), update the appropriate file in the same change so references stay in sync with the code.

### 2026-04-04 — Shared scheduler abstractions belong in `./util-repos/clockwork`

- When repo-local cron snippets or `systemd` unit-generation logic start to repeat across the portfolio, move the scheduler description/rendering into `./util-repos/clockwork` instead of cloning another shell installer.
- Keep workload wrappers, notifications, and env bootstrap in the downstream repo; `clockwork` should own the declarative manifest, rendered scheduler artifacts, and install guidance.
- New scheduling workflows should update `clockwork` examples or downstream inventories in the same change so the shared abstraction stays grounded in real portfolio use.

### 2026-04-03 — Shared utility repos should track downstream usage in config/downstream-repos.toml

- Each shared utility repo should keep a tracked `config/downstream-repos.toml` inventory of known downstream repositories.
- Record repo paths relative to the portfolio root and keep the list focused on explicit code, config, or operational integrations rather than generic `AGENTS.md` cross-references.
- Update the inventory in the same change that adds or removes a cross-repo dependency so the control plane and the utility repo stay aligned.

### 2026-04-03 — dyno-lab is optional when a repo still tests older Python versions

- `./util-repos/dyno-lab` currently declares `requires-python = ">=3.10"`.
- Repos that still run GitHub Actions on older interpreters such as Python `3.8` or `3.9` must not unconditionally import `dyno_lab.fixtures` in `conftest.py`.
- For those repos, either gate the pytest plugin load on `find_spec("dyno_lab")` or install/use `dyno-lab` only in the newer matrix lanes that support it.

### 2026-04-03 — Ignore local `.codex` artifacts in repo roots

- A repo-root `.codex` path can appear as a local agent artifact and should not be committed.
- Add `.codex` to repo `.gitignore` files when it shows up, alongside other local-only handoff or tooling artifacts such as `CHATHISTORY.md`.
- Treat `.codex` as local workspace state unless a repository explicitly documents it as committed project content.

### 2026-03-26 — GitHub CLI access is unreliable inside the sandbox

- `gh` commands that need GitHub API access may fail inside the sandbox even when local login appears to succeed.
- Verify actual API access before relying on `gh auth` output alone.
- Expect `gh repo create`, `gh repo view`, and similar networked commands to need escalated execution in this environment.
- Prefer SSH remotes for `git push` when an SSH key is already available.

### 2026-03-26 — Avoid committing absolute local filesystem paths

- Do not commit local absolute paths when relative paths or location-neutral wording communicate the same workflow.
- Prefer references like `./util-repos/traction-control`, `../..`, or "portfolio root" over machine-specific mount points.
- Treat committed local path disclosure as a documentation hygiene and security issue, not just a style preference.

### 2026-03-26 — Inventory CI by workflow presence, not only by `ci.yml`

- Some repositories satisfy the CI standard through differently named workflows such as `black-pylint-pytest.yml` or publish-specific pipelines paired with test workflows.
- When auditing CI coverage, inspect `.github/workflows/` broadly before concluding that a repository has no CI.
- Prefer reporting "no workflow files found" over "missing `ci.yml`" unless the exact filename is itself the requirement being audited.

### 2026-03-26 — Bulk portfolio pushes should fetch first and expect repo-specific doc conflicts

- Before pushing a portfolio-wide batch, fetch each modified repo first so you know which branches are behind origin.
- Generic governance commits can conflict with repo-specific `CONTRIBUTING.md` files that were added upstream after the local branch last fetched.
- When those add/add conflicts happen, preserve the repo-specific workflow guidance and fold in only the useful generic contributor hygiene instead of replacing the file wholesale.

### 2026-03-26 — Portfolio publish work is not done until post-push workflows are checked

- After pushing changes that trigger GitHub Actions, review the resulting workflow runs instead of assuming the local validation was sufficient.
- Treat new CI failures as part of the same rollout and resolve them before calling the cross-repo change complete.

### 2026-03-26 — Repo-level AGENTS files should point back to the control plane

- Include a short control-plane reference in each repo-level `AGENTS.md` so an agent working inside any individual repo can still find the portfolio-wide standards.
- Use portfolio-root relative paths such as `./util-repos/traction-control` when describing where the shared standards live.

### 2026-03-26 — Shared utility repos should be advertised from every repo AGENTS file

- Keep the portfolio-standard utility repos discoverable from any repo-level `AGENTS.md`, especially `./util-repos/auto-pass`, `./util-repos/nordility`, `./util-repos/shock-relay`, and `./util-repos/snowbridge`.
- When a repo needs password management, VPN switching, external messaging, or SMB-based file sharing, steer agents toward those shared repos before they build a bespoke implementation.

### 2026-04-01 — New shared utility repos need control-plane and repo-AGENTS updates together

- When introducing a new portfolio utility repo, add it to the `traction-control` inventory and shared-utility documentation in the same change that creates the repo.
- Update the shared-utility snippet in repo-level `AGENTS.md` files at the same time so agents working inside other repos can discover the new implementation home immediately.

### 2026-04-01 — dyno-lab is the portfolio standard for shared test utilities

- Any repo that needs subprocess mocking, env patching, filesystem fixtures, CLI capture, HTTP session mocking, schema validation, smoke scaffolding, or shared pytest markers should depend on `./util-repos/dyno-lab` instead of re-implementing the pattern locally.
- Install via `pip install -e ./util-repos/dyno-lab` or as a local path dependency in `pyproject.toml`.
- Activate pytest fixtures with `pytest_plugins = ["dyno_lab.fixtures"]` in `conftest.py`.
- Register shared markers with `from dyno_lab.markers import register_markers` in `pytest_configure`.

### 2026-03-26 — Shared architecture toolchains should live in archility, not feature repos

- Keep PlantUML/draw.io bootstrap scripts, binary wrappers, and cross-repo architecture render entry points in `./util-repos/archility`.
- Feature repos should keep their diagram sources, renders, and contributor docs, but they should not become the long-term owners of shared architecture-tooling downloads.

### 2026-03-26 — Cross-repo architecture automation works best with fixed starter paths

- Keep the starter architecture surface consistent across repos with `docs/contributor-architecture-blueprint.md`, `docs/diagrams/repo-architecture.puml`, and `docs/diagrams/repo-architecture.drawio`.
- Use `./util-repos/archility` to generate or refresh that baseline instead of inventing repo-specific filenames for the same purpose.

### 2026-03-26 — Architecture render validation should check exact artifact paths, not only file counts

- PlantUML and Draw.io can emit different default filenames than the portfolio convention unless `archility` normalizes them explicitly.
- After changing shared architecture render orchestration, verify the exact expected artifacts such as `repo-architecture.puml.svg` and `repo-architecture.drawio.svg`, not only aggregate diagram counts.

### 2026-03-27 — Architecture docs must center on the primary execution or data flow, not folder structure

- For any repo type, architecture documentation should show how data or control flows through the system — ingestion, transformation, output, and confirmation paths — not just a listing of top-level directories or module names.
- Common failure mode: a diagram that names `src/`, `tests/`, and `scripts/` without showing how they connect or what the system actually does at runtime. Add type-specific context: what enters the system, what the primary computation or curation is, and what artifacts or side-effects leave it.
- Detailed per-repo-type authoring guidance (data-ingest lifecycle, simulation-evaluation loop, secret-wrapper subprocess branches, governance loop, etc.) lives in `./util-repos/archility/LESSONSLEARNED.md` and in the `archility` blueprint templates. Use `archility generate` to produce a deterministic starter, then deepen with an agent-authored pass that reads the actual code and data flow.
- Keep both paths on the same standard layout under `docs/` (`contributor-architecture-blueprint.md`, `docs/diagrams/repo-architecture.puml`, `docs/diagrams/repo-architecture.drawio`) so shared render and audit orchestration still works across the portfolio.

### 2026-03-28 — New repos should start with seeded durable lessons, not an empty placeholder

- Initialize repo-root `LESSONSLEARNED.md` from a shared control-plane template instead of leaving only "No durable lessons recorded yet."
- Keep that template limited to universal operating guidance: document the real workflow, keep local or private boundaries explicit, and re-run repo-appropriate validation after CI-facing changes.
- When the shared baseline changes, backfill those lessons into existing repos unless a repo-specific entry already captures the same rule more precisely.

### 2026-03-30 — Normalize SVG EOF in archility, not repo by repo

- Missing terminal newlines in generated `.svg` artifacts recur across downstream repos because the renderer is the shared source of truth for those files.
- Fix that class of failure in `./util-repos/archility` by normalizing final SVG outputs during the shared render handoff instead of chasing `end-of-file-fixer` failures repo by repo after pushes.

### 2026-04-01 — SSH key transfer to mobile devices should use snowbridge, not email or cloud

- When bootstrapping SSH access from a mobile device, copy the private key to the snowbridge SMB share so the phone can retrieve it via Files app over the LAN.
- Delete the key from the share immediately after the device imports it — the share is a transit medium, not a key store.
- The `authorized_keys` entry remains on the server; only the private key moves transiently through the share.
- This pattern avoids email, iCloud, or third-party cloud exposure of the private key while keeping the workflow simple.

### 2026-04-01 — WireGuard tooling should live in its own utility repo, not inside the service it protects

- When a setup tool (like WireGuard installation) is embedded inside a specific service repo (like snowbridge), it becomes invisible to other repos that could use the same tool.
- Extract standalone installer/configuration tools into their own dedicated utility repos so they can be used across multiple service repos without duplication.
- Keep the host-specific config templates (example and local files) in the service repo where they are semantically owned; move only the generic installer and tooling to the utility repo.
- Document the split clearly in both repos: the service repo keeps its configs and references the utility repo for the tooling.

### 2026-04-01 — Architecture diagram authoring rules (PlantUML and draw.io)

- **PlantUML**: avoid reserved activity-diagram keywords (`init`, `end`, `start`, `stop`, `fork`, `join`, `kill`) as element aliases — they silently switch the interpreter to activity mode and break `rectangle` + `-->` arrow syntax. Use names like `publicapi` or `pkg_init` instead.
- **PlantUML**: prefer `!pragma layout elk` over `smetana` for component/package diagrams with cross-package arrows. `elk` is bundled and handles cross-boundary `rectangle` links reliably; `smetana` fails on those cases.
- **draw.io**: always add `overflow=hidden` to cell styles (leaf cells and swimlane containers) and size with adequate height — at font size 12, allow at least 22 px per line plus 16 px padding (3-line box ≥ 82 px). Never commit diagrams with "Focus Root" placeholder labels.
- Prefer plain identifier text in draw.io box labels over Markdown backticks; backticked text formats oddly in direct PNG exports even when SVG looks fine.

### 2026-04-02 — Portfolio-wide test audit and dyno-lab integration

- Flat-module repos (no src/ layout) need `pythonpath = ["."]` in
  `[tool.pytest.ini_options]` so `pytest` resolves local imports without
  requiring `PYTHONPATH=.` to be set manually.
- A root `conftest.py` that calls `sys.path.insert(0, str(Path(__file__).parent))`
  achieves the same for repos where adding a full `pyproject.toml` is heavy.
- Test files named `*_validator.py` are NOT collected by pytest's default
  naming convention — always use `test_*.py` or `*_test.py`.
- dyno-lab can be referenced as a CI dev dep via:
  `pip install git+https://github.com/casonk/dyno-lab.git`
  until it is published to PyPI.

### 2026-04-03 — Always run ruff format after ruff --fix

- `ruff check --fix` fixes lint violations (F401, I001, etc.) but does NOT apply the
  black-style formatter.
- Always follow with `ruff format <files>` (or `ruff format .`) before committing,
  otherwise `ruff format --check` in CI will fail on a separate step.
- Quickest pattern: `ruff check --fix . && ruff format .`

### 2026-04-04 — Portfolio-wide Python formatter and linter canonical config

- Standard `pyproject.toml` config for every Python repo: `[tool.ruff]` with `line-length = 100`; `[tool.ruff.format]` with `quote-style = "double"` and `indent-style = "space"` (note: `line-length` is rejected by ruff in `[tool.ruff.format]` — it must live in the parent `[tool.ruff]` table); `[tool.black]` with `line-length = 100` and repo-appropriate `target-version`.
- Standard `[tool.ruff.lint]`: `select = ["E","F","I","UP","B","SIM"]`, `ignore = ["E501","B008","SIM108"]`. Add `SIM117` to `ignore` for test-heavy repos — nested `with` blocks in tests are often intentionally separate for readability. `ruff` target-version is a string (`"py310"`); `black` target-version is a list (`["py310"]`).
- Pre-commit ordering: `ruff-format` (preferred) BEFORE `black` (deprecated). CI ordering mirrors this: `ruff format --check .` (primary enforcing step) BEFORE `black --check --diff .` (deprecated, non-fatal when genuine incompatibility exists).
- Workflow: `ruff check --fix --unsafe-fixes . && ruff format . && black .` in that order. For repos without `pyproject.toml`, add `--line-length 100` flags to all ruff and black CLI commands.
- When both formatters coexist and genuinely disagree (multiline `if`-condition wrapping, magic trailing comma), use `# fmt: skip` on the offending line or extract the construct to a named variable — both formatters then agree or skip.
- Before running any formatter on a repo, check its CI step to confirm which formatter it enforces; the repo's `AGENTS.md` "Local CI Verification" section has the exact commands.

### 2026-04-03 — Inline `||` with quoted colon in GitHub Actions `run:` breaks YAML

- A one-line `run: command || echo "message: with colon"` is invalid YAML — the `:` inside
  the double-quoted string causes a YAML mapping-values-not-allowed parse error.
- The workflow fails with 0 jobs (nothing runs) and no diagnostic message.
- Fix: always use `run: |` block scalar when the command contains `||`, `&&`, or any string
  with a `:` that might be mistaken for a YAML key.
- Validate locally with `python3 -c "import yaml; yaml.safe_load(open(f).read())"` before pushing.

### 2026-04-04 — Use a clean worktree for CI repairs when the main checkout is dirty

- If a pushed branch exposes formatter or CI failures but the local checkout already has unrelated
  in-flight edits, create a clean detached worktree from the pushed head and fix the branch there.
- This keeps unrelated local work out of the repair commit and avoids having to stash or reset a
  dirty repo just to clear hosted CI.
- After the repair commit is pushed and hosted checks are green, remove the temporary worktree and
  leave the original checkout state unchanged.

### 2026-04-04 — Standardize repo-local profiling around tachometer

- The portfolio baseline for local profiling is now:
  `config/tachometer/profile.toml`, `scripts/run_tachometer_profile.sh`, and a
  `.gitignore` entry for `.tachometer/`.
- Keep profiling outputs local-only under `.tachometer/`; commit the manifest
  and wrapper, not the generated host-specific profile or summary JSON.
- When a repo needs repo-level or command-level profiling, prefer integrating
  with `./util-repos/tachometer` instead of adding another local profiler or
  ad hoc resource snapshot script.

### 2026-04-04 — Cross-repo tests must not depend on workstation layout or local env files

- If a test imports helpers from a sibling repo, make the import deterministic in
  CI and temporary worktrees: either clear stale modules before importing from the
  explicit sibling `src/` path or inject the needed dependency through `PYTHONPATH`
  during verification.
- If behavior depends on a local config file such as `auto-pass.env.local`, create
  a temporary fixture file in the test instead of assuming the developer machine
  already has one.
- Run at least one clean-environment verification pass before pushing repo-wide
  rollouts so path-coupled or host-coupled tests fail locally instead of only in
  hosted CI.

### 2026-04-11 — Restart active user timers after changing installed systemd calendar units

- Re-rendering a timer unit into `~/.config/systemd/user` and running
  `systemctl --user daemon-reload` plus `enable --now` is not enough when the
  timer is already active; the old schedule can remain in memory.
- After changing `OnCalendar`, explicitly run `systemctl --user restart
  <timer>.timer`, then verify both `systemctl --user status` and
  `systemctl --user list-timers --all` so the next trigger matches the new
  calendar expression.

### 2026-04-12 — Audit REFS drift by scanning both tracked and gitignored files

- `REFS-PUBLIC.md` drift is easy to miss because the starter template still looks structurally valid; scan for the placeholder domains and comments (`example.com`, `docs.example.com`, `github.com/org/repo`) instead of only checking file presence.
- `REFS-LOCAL.md` drift hides even more easily because those files are gitignored; a portfolio cleanup should scan and refresh both tracked `REFS-PUBLIC.md` and local-only `REFS-LOCAL.md`, not just the tracked side.
- For repos with no durable external upstreams, replacing the starter comments with a repo-specific "no standing public refs required" statement is better than leaving the generic template in place.

### 2026-04-12 — Publish dirty cross-repo rollouts from clean sibling worktrees and align formatter pins across local and hosted CI

- When many portfolio repos have unrelated local WIP, publish cross-repo documentation rollouts from clean sibling worktrees such as `<repo>.codex-publish`; that keeps commits scoped to the intended changes and prevents bundling unrelated edits from the main checkout.
- A full-repo `pre-commit run --all-files` in those clean worktrees will surface baseline drift that the dirty checkout may be hiding, including whitespace debt, invalid example YAML, brittle tests tied to a checkout directory name, and repo-local import assumptions. Treat those as real blockers and fix them in the clean worktree instead of weakening verification.
- If hosted CI installs formatter packages directly, pin the workflow formatter versions and arguments to match `.pre-commit-config.yaml` exactly. Otherwise a repo can pass local `pre-commit` but fail GitHub Actions on the same commit because the workflow pulled a newer or differently configured formatter.

### 2026-04-19 — SVG files generated by archility must end with a trailing newline

- PlantUML-generated `.svg` and `.puml.svg` artifacts sometimes omit the final newline, causing the `end-of-file-fixer` pre-commit hook to fail in CI.
- After rendering new diagram artifacts, run `pre-commit run end-of-file-fixer --all-files` before committing to catch this class of failure before push.
- The fix is in `archility`'s SVG normalization path — each new render pass should ensure final-newline compliance so downstream repos don't accumulate this failure.

### 2026-04-19 — Clean-worktree publish sweeps leave all main checkouts stale but that is expected

- After a portfolio-wide sweep that publishes from clean sibling worktrees, every main checkout will still show `behind=N` and local dirty tracked files that match what is already on origin/main.
- This is expected and not a blocker: the remote is correctly updated. The local dirty state is just the working tree having content that matches origin/main while the local `HEAD` pointer still points to the pre-sweep commit.
- Do not mistake "git status shows dirty" for "changes not yet published" — always compare against `git diff origin/main` to see what is genuinely unpublished.

### 2026-04-20 — For current private site inventories, trust local service registries over README examples

- For "what websites are live right now?" requests, use the local source of truth such as `wiring-harness/services.local.toml`, generated Caddyfiles, repo-local `settings.env`, and live listeners instead of relying on README example hostnames.
- Private infra READMEs can legitimately lag the local machine state; when they disagree, report the live configured hostname and explicitly call out the stale example so it does not propagate further.

### 2026-04-21 — If private hostnames are shared across repos, centralize them in one registry and have sibling repos consume it

- Once multiple repos refer to the same private browser/admin surfaces, a docs-only inventory is not enough; one repo should own the canonical hostname registry and sibling repos should resolve from it instead of keeping parallel hostname lists.
- For this portfolio, `wiring-harness/services.toml` plus `services.local.toml` is the right ownership point because it already governs shared Caddy, private DNS, and mTLS issuance.

### 2026-04-21 — Headless sessions cannot be assumed to have clipboard support

- Terminal-side clipboard tools like `wl-copy` may be installed but still unusable in headless sessions when `XDG_RUNTIME_DIR` / `WAYLAND_DISPLAY` are unset.
- When the user needs copyable commands from a headless session, prefer email or a local file fallback instead of assuming clipboard repair is possible from the terminal.

### 2026-04-21 — Triage failing clockwork jobs by separating scheduler defects from underlying workload failures

- A failed user timer/service is not automatically a `clockwork` bug. Check `systemctl --user status` and `journalctl --user-unit` first to see whether the failure is at the unit layer (`203/EXEC`, missing env, bad working directory) or inside the wrapped workload.
- For wrapper-style jobs, follow the nested `Run metadata:` and `Run log:` paths before changing scheduler manifests. Many failures are real downstream alerts such as expired tokens, checksum drift, MFA/TTY requirements, or repo-audit findings.
- Fix unit-layer defects in the shared scheduler/manifests; leave genuine workload failures as repo-specific alerts unless the user asked to repair the downstream pipeline too.

### 2026-04-21 — Timestamped current-state snapshots should not be verified against statement checksum manifests by default

- `verify-runs` against a local statement checksum manifest is appropriate for manifest-stable statement files, not for timestamped API snapshot outputs whose filenames change every run.
- If a scheduled job captures current-state JSON snapshots like `...-20260421T163038Z.json`, default that scheduler path to `--skip-checksum-verify` or equivalent and make checksum verification an explicit opt-in.
- Otherwise the job will fail with `target_not_in_manifest`, which looks like a scheduler failure but is really a mismatched verification policy.

### 2026-04-21 — Portfolio baseline docs and the daily audit must evolve together

- If `AGENTS.md` says a file is part of the standard repo baseline, the portfolio audit must check for it explicitly. Otherwise the written standard can drift far ahead of reality without any scheduled job flagging it.
- When adding a new baseline file like `BACKLOG.md`, update both the template set and `scripts/portfolio-audit.sh` in the same change.
- Exclude vendored third-party git repos from baseline enforcement unless you intentionally want to fork and normalize them.

### 2026-04-21 — Use shock-relay as the email fallback when Gmail MCP tools are unavailable

- Gmail MCP tools can disconnect mid-session; when that happens, send email via shock-relay instead of blocking or abandoning the task.
- Command: `python3 SHOCK_RELAY_ROOT_PLACEHOLDER/services/gmail-imap/send_email.py <to> <subject> <body>`
- The config is at `./util-repos/shock-relay/services/gmail-imap/config.local.yaml` and is already provisioned — no setup required.

### 2026-04-22 — Sibling-repo shims and test helpers must resolve through the git common dir to stay worktree-safe

- Clean publish worktrees under `/tmp` break any repo code or tests that assume a sibling utility repo lives next to the checked-out worktree path.
- When a repo needs a sibling like `dyno-lab` or `tachometer`, first try the normal local path search, then fall back to `git rev-parse --git-common-dir` and search upward from the real shared repo path behind the worktree.
- This keeps `pytest -q` and local compatibility shims valid in clean worktrees without forcing editable installs or special-case `PYTHONPATH` setup during cross-repo publish sweeps.

### 2026-05-08 — Caddy TLS cert changes require a full service restart, not a reload

- `systemctl --user reload caddy` does not re-read certificate files when cert content changes (e.g. after renewal or replacement). Only a full restart picks up the new certs.
- After updating or renewing a Caddy-managed TLS certificate, run `systemctl --user restart caddy` and confirm the new cert expiry in the browser or with `openssl s_client`.
- Applies to the shared Caddy instance managed by `./util-repos/wiring-harness`; any repo adding a Caddyfile.d drop-in should note this in its own deployment docs.

### 2026-05-08 — All new internal services must integrate via Caddyfile.d drop-in, not a competing proxy

- This machine runs a single shared Caddy instance managed by `./util-repos/wiring-harness` as the portfolio reverse proxy. All new internal web services must be wired in via a `Caddyfile.d/` drop-in rather than installing nginx, another Caddy instance, or any competing HTTP server on the same ports.
- Before adding any web-facing service, confirm what is already on 80/443: `ss -tlnp | grep ':80\|:443'` and `systemctl list-units --type=service --state=running | grep -iE 'http|web|caddy|apache'`.
- A conflicting port 80/443 bind will prevent the shared Caddy from starting after a reboot; competing proxy installs cause silent routing failures.

### 2026-05-08 — systemd service units require the execute bit on wrapped shell scripts

- A systemd service that calls a shell script via `ExecStart=` fails silently with `203/EXEC` if the script does not have the execute bit set.
- After creating or updating a service's wrapper script, run `chmod +x <script>` and then `systemctl --user daemon-reload` + `systemctl --user restart <service>` to confirm the unit starts cleanly.
- When shipping a new systemd service in a repo, include `chmod +x` in the install docs or Makefile target so the failure does not occur during setup.

### 2026-05-08 — systemctl user commands need the user's D-Bus environment

- `systemctl --user status`, `is-active`, `list-units`, and `list-timers` can run as the service owner without elevated privileges, but they still need access to the user's D-Bus session.
- `systemctl --user restart`, `stop`, `start`, `daemon-reload`, and `enable` mutate unit state and also need the same user-bus context; they fail from agent subprocesses when `$DBUS_SESSION_BUS_ADDRESS` / `$XDG_RUNTIME_DIR` are unset.
- In this Codex environment, if `/run/user/1000/bus` exists, use `XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus systemctl --user <command>` and run it outside the sandbox when required.
- `systemctl --machine=user@.host --user ...` may be suggested by the error text but can still fail with machine-transport permission errors; prefer the explicit user-bus environment first.
- In automation scripts that read then conditionally act on unit state, separate the read check from the mutating action and document that the action requires the correct user context.

### 2026-06-07 — CI failure monitors and repair timers are separate loops

- The Gmail-based GitHub CI monitor detects and files failure emails; when repair triggering is enabled, it should schedule `ci-repair-agentic.service` with a delay rather than starting it inline.
- Keep the delay long enough for the change-making agent or human to finish the current push before the autonomous repair pass scans for remaining failures.
- When a user expects "fixed" notification emails, the monitor needs an explicit resolved-state check against GitHub Actions plus an opt-in recipient such as `GITHUB_CI_FIXED_NOTIFY_TO`.
- A repaired repo should only be announced as fixed after a later default-branch SHA has completed green, not merely because an older push workflow was green while a scheduled workflow failed on the same SHA.

### 2026-06-07 — CI monitor email filing should mirror intake's processed/notify split

- Inbound GitHub CI failure emails should be filed out of INBOX into a processed folder after parsing, not merely copied to a Gmail label.
- Monitor-generated notification emails should use a separate notify folder and a grace window before filing so device notifications have time to fire.
- Processed failure filing can mark messages read; notify filing should not mark generated notification emails read.
- Fixed-CI notifications should be gated on the new processed-folder filing timestamp, not historical label-copy state, so enabling the feature does not back-send notifications for every old failure in the monitor state file.

### 2026-06-07 — Disable working-tree timers while editing their scripts

- User-level timers installed from this repo execute the checked-out working tree, not only committed code.
- Before editing a timer-owned script such as `monitor_github_ci_emails.py`, stop the corresponding user timer; restart it only after validation and push.
- This avoids a live timer running partially implemented code and causing duplicate emails or other side effects.

### 2026-06-07 — Generated CI notifications should be digestable separately from inbound GitHub mail

- The Gmail CI monitor can file inbound GitHub failure messages, but generated fixed-CI notices should support digest queueing so a burst of repaired repos does not become a burst of outbound email.
- Keep the distinction clear: GitHub-originated failure emails may still need GitHub/Gmail-side filtering, while monitor-generated notifications can be controlled in repo code.

### 2026-06-08 — Reuse `crew-chief` for local multilingual translation before paying for API translation

- When a repo only needs literal translation of non-English OCR or email text, default to the shared local `crew-chief` Ollama service instead of a paid API path; this keeps token usage and recurring fees down while preserving a consistent portfolio-wide local inference surface.
- Keep the local translation output as secondary metadata and preserve the original source text separately for audit/debugging; deterministic parsing and higher-trust correction flows should remain the source of truth for totals, currencies, and other transactional fields.
- When integrating from another repo, prefer importing `crew-chief`'s stdlib-only client from the sibling checkout rather than re-implementing another ad hoc Ollama HTTP wrapper.

### 2026-06-09 — Prefer `intake translate` for receipt-domain translation reuse

- When another repo needs English translation of receipt OCR text or receipt images, prefer shelling out to `intake translate text|receipt --json` instead of cloning receipt-specific OCR/translation prompts in each repo.
- Use the owning application's audited backfill command for historical
  receipt locale/translation cleanup so database updates, sidecars, and
  downstream private analytics sync stay on reviewed repository paths rather
  than in one-off notebooks or ad hoc scripts.
- Keep `crew-chief` as the generic local LLM substrate; use `intake` as the receipt-domain service layer that wraps receipt OCR, locale hints, sidecar syncing, and safer backfill policy.
- Treat example/local configuration separation as a validated invariant, not a
  naming convention. Network examples should use IANA documentation ranges or
  explicit placeholders, and repository checks should reject private or known
  deployment values in tracked example files.

### 2026-07-26 — Gitignore is not replication authority

- A Git-ignored path is only absent from ordinary Git snapshots; that does not
  authorize another process to read, back up, or replicate it. Enroll ignored
  state through exact private selectors with explicit size and file limits.
- Assign each selected path to exactly one protection level. Highest-sensitivity
  mesh-only data must never fall back to a hosted destination, and a nearby
  node is only a route—not proof of writer authority or safe failover.
- Bind backup tools to immutable staged bytes and explicit credentials. Hashing
  a live pathname before handing that pathname to another process leaves a
  symlink/rename race that can copy data outside the reviewed selection.

### 2026-07-27 — Backup acknowledgement is not portable restore proof

- Archive a canonical manifest inside the client-encrypted snapshot and bind
  committed state to that manifest. Paths, sizes, original modes, and content
  hashes are portable evidence; inode, device, UID, GID, and timestamps are
  host-specific capture metadata and must not define cross-node identity.
- Prove recovery by checking repository data, restoring the exact snapshot
  recorded by committed state into a new private directory, and rejecting
  missing, extra, linked, traversing, or hash-mismatched nodes. A successful
  backup process and a syntactically valid snapshot ID are only acknowledgements.
- A backup repository's newest snapshot is not the transaction commit record.
  Treat the locally committed state generation and its full snapshot IDs as
  authoritative, inspect each exact ID and its identity tags, and restore that
  exact snapshot. Newer below-quorum snapshots are uncommitted orphans and must
  not invalidate proof of the last committed generation. Exercise this with a
  second real generation and disjoint below-quorum orphan regressions.

### 2026-07-28 — Podman is a node runtime, not the WireGuard mesh

- Put WireGuard on each native Linux host and bind a rootless Podman target only
  to the private address that host actually owns. Podman bridge networks are
  local container networking; they do not establish multi-host membership,
  routing, failure-domain independence, or writer authority.
- Treat macOS Podman Machine as a container-verification environment unless
  host-to-VM routing is separately designed and proven. Its containers run in
  a Linux VM and cannot be assumed to bind the Mac's WireGuard interface.
- Render one target-specific Quadlet bundle at a time and omit `[Install]`
  until activation has a separate reviewed host/network gate. Replacing a
  Podman secret does not update an existing container, so key rotation must
  recreate the target before the old credential is revoked.
- Pass rendered `.container` and `.volume` files through the real Quadlet
  generator, not only string assertions. Keep full credential validation,
  host-key/`known_hosts` binding, and bounded storage capacity as explicit false
  manifest gates until an activation workflow can prove them transactionally.

### 2026-08-11 — Cross-repo coordinators should stage inputs, not fork security renderers

- Keep each security-sensitive renderer in its owning repository and invoke its
  current public CLI unchanged. Copying renderer code into an orchestrator
  creates an unaudited fork and makes later safety fixes inconsistent.
- Probe the exact sibling CLI contract and record the worktree commit, dirty
  state, and renderer-source hashes in every immutable generation. If the
  expected flags or artifact shape change, fail closed instead of bypassing an
  owning repo's path or validation boundary.
- Stage machine-specific inputs and aggregate evidence in owner-only ignored
  paths. Treat a failed generation as consumed, and keep live activation in a
  separately reviewed workflow; render composition must not quietly become
  service, firewall, credential, network, or failover mutation.
- Prove the composition with an allowlisted, networkless, read-only container
  that runs the real owning CLIs. Synthetic namespace-local interfaces and
  credentials can exercise exact binding and Caddy validation, but the test
  must state that it does not prove macOS service activation, a real tunnel, or
  writer authority. Cover both successful and failed generation immutability,
  not only the happy-path artifact text.

### 2026-08-11 — Optional coordinator slices must gate the whole owned dependency path

- A strict capability switch must gate its filesystem prerequisites, sibling
  source/API probes, staged inputs, child invocation, logs, artifact checks,
  and output immutability boundary together. Skipping only the child still
  leaves a nominally disabled capability blocked by or coupled to host state.
- Keep adjacent capabilities on separate booleans and test the asymmetric
  case directly. For Snowbridge on Air, native SMB disabled plus web enabled
  must omit every SMB/PF render artifact while retaining the reviewed
  loopback `:8080` web edge.
- Require the new boolean explicitly instead of supplying a parser fallback.
  Record a disabled capability and its reason in immutable evidence; disabled
  means “not audited or rendered,” never proof that the omitted host surface is
  safe.

### 2026-08-29 — Regenerate accepted-secret baselines after a history rewrite

- Gitleaks fingerprints bind findings to commit IDs, so any filter-repo rewrite
  invalidates the matching baseline even when a finding's content is unchanged.
  Regenerate the baseline from the rewritten full history, then validate a second
  full-history scan with that new baseline before publishing it.
- A baseline file may be scanned by a custom rule itself. Preserve and validate
  those resulting accepted fingerprints rather than hand-editing the baseline or
  disabling the rule; the post-regeneration scan is the stability check.

### 2026-08-29 — Empty Bash-3 arrays need guarded iteration under nounset

- Bash 3 treats direct expansion of an empty array in a `for` list as unset
  when `set -u` is active, even though its length can be read safely. Use a
  conditional array expansion or a length guard for no-op inventory paths, and
  retain an empty-inventory regression test for every scheduled audit wrapper.
