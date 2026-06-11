# LESSONSLEARNED.md — Portfolio Control Plane

> Purpose: record durable lessons that should change how future agents work across the portfolio.
> Unlike `CHATHISTORY.md`, this file is tracked and should keep only reusable lessons.

## How To Use This File

- Read this file before repeating setup, publishing, audit, or automation workflows.
- Add lessons that generalize beyond a single session.
- Keep entries concise and action-oriented.
- Do not use this file for transient status updates or full session logs.

## Lessons

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

- The `.gitleaks-baseline.json` file records known findings with their `Match` and `Secret` values verbatim, which include the local machine path `/mnt/4tb-m2/git/`.
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
- `/dev/github/GitHub-PAT-AGENT` (fine-grained, all repos, code read + workflows write) is the correct long-term token for portfolio CI secrets; `/dev/github/GitHub-PAT-MCP` (classic, `repo` scope) is the fallback.

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

- When bootstrapping SSH access from a mobile device, copy the private key to the snowbridge SMB share (`/mnt/4tb-m2/read/ssh-keys/`) so the phone can retrieve it via Files app over the LAN.
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
- Use `intake translate backfill` for historical receipt locale/translation cleanup so DB updates, sidecars, and downstream personal-finance sync stay on the audited repo path rather than in one-off notebooks or ad hoc scripts.
- Keep `crew-chief` as the generic local LLM substrate; use `intake` as the receipt-domain service layer that wraps receipt OCR, locale hints, sidecar syncing, and safer backfill policy.
