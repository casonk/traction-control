# LESSONSLEARNED.md — Portfolio Control Plane

> Purpose: record durable lessons that should change how future agents work across the portfolio.
> Unlike `CHATHISTORY.md`, this file is tracked and should keep only reusable lessons.

## How To Use This File

- Read this file before repeating setup, publishing, audit, or automation workflows.
- Add lessons that generalize beyond a single session.
- Keep entries concise and action-oriented.
- Do not use this file for transient status updates or full session logs.

## Lessons

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

### 2026-04-07 — Pin ruff version consistently between local dev, pre-commit, and CI

- Different ruff versions can disagree on import sort order (I001) and formatting choices, causing local-clean code to fail CI.
- When the portfolio standardizes on a new ruff version, update the CI `pip install ruff==X.Y.Z` pin and the `.pre-commit-config.yaml` `rev: vX.Y.Z` in the same change.
- Always validate locally with the same ruff version that CI uses: `pip install ruff==0.15.9` before running `ruff check .` and `ruff format --check .` before any push.
- Never push code that has only been validated by a mismatched ruff version.

### 2026-04-07 — Run pre-commit locally before every push; repo AGENTS.md specifies exact commands

- Every repo's `AGENTS.md` now contains a "Local CI Verification" section with the exact commands to run before pushing.
- The portfolio-wide rule (traction-control AGENTS.md rule 8) is non-negotiable: do not push code that has not passed local verification.
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

### 2026-03-27 — Starter PlantUML diagrams should avoid hard dependency on Graphviz

- The shared `repo-architecture.puml` baseline is more portable when it uses PlantUML's Smetana layout instead of depending on a machine-local `dot` install.
- Reserve Graphviz-dependent layouts for richer repo-specific diagrams that actually need them.

### 2026-03-27 — Archility should keep deterministic and agentic architecture paths separate

- Treat `archility generate` as the deterministic programmatic path that derives starter architecture strictly from repository structure and code markers.
- Treat deeper AI-authored repository architecture as a separate, intentionally non-deterministic path that follows full repository inspection and understanding.
- Keep both paths on the same standard file layout under `docs/` so shared render/audit orchestration still works across the portfolio.

### 2026-03-27 — Draw.io PNG exports should avoid backticked label text

- Draw.io's direct PNG export can format backticked identifiers oddly even when the matching SVG looks fine.
- When checked-in PNG render quality matters, prefer plain identifier text in draw.io box labels instead of Markdown-style backticks.
- Keep the shared draw.io wrapper in `archility` friendly to headless environments by passing `--no-sandbox`.

### 2026-03-27 — Taxonomy-heavy archive repos need grouped architecture starters

- Repositories whose top-level structure is a large coded taxonomy, such as course archives, should not be summarized as only the first few directories.
- Prefer grouping those repos by their stable prefixes, such as `CSC/`, `ECN/`, `INB/`, and `MTH/`, and then listing the course directories inside each subject area.
- Keep the shared `archility` starter aware of that pattern so the baseline diagram is readable before any deeper agent-authored architecture pass.

### 2026-03-27 — Facade-based repos need architecture from internal module flow, not only top-level folders

- When a repo exposes a stable public entrypoint that re-exports internal modules, do not describe it as monolithic or reduce the architecture to the visible folder list.
- Inspect the internal module graph, the ingestion paths, and the downstream consumers before rewriting the architecture docs.
- Generated starter diagrams are an acceptable baseline, but repos with a facade-plus-internal-modules design need a deeper pass that shows the real code and data flow.

### 2026-03-27 — Scrape-to-schema repos need diagrams built around the data lifecycle

- For data-ingest applications, the architecture should show how external sources become intermediate frames, staging tables, normalized tables, and downstream analytics or graph views.
- A module list alone is too vague for repos whose real implementation value is in the import pipeline and schema transitions.
- When rewriting those diagrams, inspect the orchestrator entrypoint and the canonical ingest function before deciding what the architecture centers on.

### 2026-03-27 — Simulation libraries need diagrams built around the experiment loop

- For research libraries, the architecture should show how inputs are validated, simulations are generated, algorithm backends are invoked, and metrics are aggregated.
- Package layout and tests matter, but they are secondary to the synthesis-and-evaluation loop when explaining the implementation.
- When rewriting those diagrams, inspect the public package exports, the generator class, the evaluator class, and the invariant tests before deciding what the architecture centers on.

### 2026-03-27 — Refresh-oriented repos need architecture that separates automatic generation from manual publication

- For repos that download data and generate large local artifact corpora, show the automatic runtime boundary explicitly.
- Distinguish pipeline-owned outputs such as `data/` and `viz/` from curated repo-root artifacts that are copied or published manually after review.
- Do not imply that a refresh command updates tracked README-facing artifacts unless the code path actually performs that publish step.

### 2026-03-27 — Multi-provider utility repos need architecture centered on adapter families

- For messaging or integration utilities, do not stop at the top-level `services/` folder list when the real implementation is several transport families with different execution models.
- Distinguish direct subprocess wrappers, shared HTTP helper layers, and protocol-specific mail adapters when those are the actual seams in the codebase.
- Include end-to-end confirmation scripts in the architecture when they are the main integration harness rather than optional examples.

### 2026-03-27 — Control-plane repos need architecture centered on the governance loop

- For governance repos like `traction-control`, the real implementation is often a policy-and-continuity workflow rather than a local executable.
- Center the architecture on the scan boundary, shared utility references, target-repo remediation flow, verification steps, and continuity updates.
- Make it explicit when the repo is documentation-driven today and reserve local automation boxes for code that actually exists.

### 2026-03-27 — Crash-triage repos need architecture centered on the evidence loop

- For local debug toolkits, a shell-folder inventory is too vague to explain the implementation.
- Center the architecture on the orchestrator, the captured evidence bundle, the heuristic analysis stage, the remediation helpers, and the local handoff log.
- When the repo also contains broader hardware or software audits, show those as separate sidecar lanes rather than pretending they are part of the main crash-snapshot pipeline.

### 2026-03-28 — Secret-wrapper utility repos need architecture centered on config, context, and subprocess branches

- For utilities that wrap external secret-management CLIs, do not stop at the package or module list.
- Show how local config and environment overrides become the effective runtime context, including any local-only cache or prompt path used to materialize secrets safely.
- Separate the read and write subprocess lanes when the implementation has materially different behaviors such as lookup, create-group, edit, and add-on-miss flows.

### 2026-03-28 — Documentation archive repos need architecture centered on curation and evidence surfaces

- For archive-style documentation repos, the real implementation is often the curation workflow rather than any executable code.
- Center the architecture on the canonical index, provider or category drilldown indexes, the grouped local evidence buckets, and the external proof links they curate.
- Keep local archived artifacts distinct from public verification links so the diagrams reflect both the stored evidence and the outward-facing catalog.

### 2026-03-28 — Architecture-tooling repos need diagrams centered on the lifecycle they orchestrate

- For repos like `archility`, a vague `src/` and `tests/` summary hides the real implementation.
- Center the architecture on the public CLI surface, the inspect / scaffold / render lifecycle, the agentic authoring boundary, the shared toolchain bootstrap, and the target-repo artifacts produced downstream.
- Treat tests, CI, and reference docs as validation and contributor-support layers around that lifecycle rather than as the primary architecture.

### 2026-03-28 — Static Jekyll sites need architecture centered on content assembly and offline authoring helpers

- For portfolio sites like `casonk.github.io`, a folder inventory is too vague to explain the real implementation.
- Center the architecture on the authored pages and collections, config and structured data, layouts and includes, the Jekyll build into `_site/`, and any optional helper lanes that precompute content or data before the site build.
- Keep private local sources, reference-only config examples, and excluded helper outputs explicit so the diagrams distinguish the public site pipeline from offline authoring workflows.

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

### 2026-04-01 — Pin ruff version in pre-commit and CI to avoid format drift

- Mismatched ruff versions between local pre-commit and hosted CI produce implicit string-concat formatting differences that pass locally but fail `ruff format --check` in CI.
- Pin the `rev:` in `.pre-commit-config.yaml` and install the same pinned version in CI instead of installing latest.
- When upgrading ruff, update both together and re-run pre-commit before pushing.

### 2026-04-01 — Avoid reserved PlantUML keywords as element aliases

- `init` is a reserved PlantUML keyword (initial pseudostate) that silently switches diagram interpretation to activity mode, breaking `rectangle` + `-->` arrow syntax.
- Avoid `init`, `end`, `start`, `stop`, `fork`, `join`, `kill`, and other activity-diagram keywords as element aliases in component/package diagrams.
- Use descriptive names like `publicapi`, `pkg_init`, or `entrypoint` instead.

### 2026-04-01 — Use elk layout for cross-package arrows in PlantUML

- `!pragma layout elk` handles cross-package arrows reliably and matches the portfolio's established diagram style.
- `smetana` is more portable but has bugs with `rectangle` elements linked across package boundaries.
- Reserve `smetana` only for simple diagrams with no cross-package relationships.

### 2026-04-01 — draw.io cells must use overflow=hidden and adequate sizing

- Always add `overflow=hidden` to draw.io cell styles — on both leaf cells and swimlane containers.  Without it, text visually escapes the block boundary in the exported SVG/PNG.
- Size every cell with adequate height for its text content.  At font size 12, allow at least 22px per line plus 16px padding (a 3-line box needs ≥ 82px height; a 2-line box needs ≥ 60px).
- Never size a swimlane container smaller than its tallest child row plus the `startSize` header plus row margins.
- Replace "Focus Root" archility template placeholder labels with actual module or component names before the diagram is committed.

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

### 2026-04-03 — dyno-lab API signatures (from integration test authoring)

- `SubprocessPatch(side_effect)` — takes a **callable** as the first arg, not
  keyword `returncode=`/`stdout=`. Wrap `build_completed_process` in a lambda:
  `SubprocessPatch(lambda *a, **kw: build_completed_process(0, "out", ""))`.
- `EnvPatch({"KEY": "val"})` — positional dict, not keyword args.
  `clear=True` wipes the entire environment for the block.
- `TempWorkdir()` has no `cd=` parameter; use `.path` attribute for the
  directory, or `os.chdir(ctx.path)` if a chdir is needed.
- `load_module_by_path(path, name, repo_root=REPO_ROOT)` is a drop-in
  replacement for hand-rolled `importlib.util.spec_from_file_location` patterns.

### 2026-04-03 — dyno-lab v0.2.0 new modules and CI pre-flight patterns

- `dyno_lab.preflight` adds `requires_tool`, `requires_env`, `requires_import` pytest marks
  that auto-skip tests when tools/env vars/packages are absent. Add
  `pytest_plugins = ["dyno_lab.fixtures"]` to `conftest.py` to activate the hook.
- `PreflightSuite` can be run as a standalone CI step before pytest to surface environment
  problems early (missing binaries, unset keys, unreachable ports).
- `dyno_lab.time.FrozenTime` freezes `time.time()`, `time.monotonic()`, and
  `datetime.datetime.now()` via a subclass override — no freezegun dependency required.
- `dyno_lab.time.FastSleep` replaces `time.sleep()` with a no-op and records all calls;
  use `fs.total_slept` / `fs.call_count` to assert retry/backoff timing.
- `dyno_lab.log.LogCapture` captures Python logging records; use `assert_logged(level, fragment)`
  and `assert_not_logged(level, fragment)` to assert on log output without relying on stdout.
- `dyno_lab.patch.AttrPatch(obj, attr=value)` patches and auto-restores object/class/module
  attributes; if the attribute didn't exist before, it is deleted on exit.
- All four modules are exported from `dyno_lab` top level; 174 internal tests pass.

### 2026-04-03 — Always run ruff format after ruff --fix

- `ruff check --fix` fixes lint violations (F401, I001, etc.) but does NOT apply the
  black-style formatter.
- Always follow with `ruff format <files>` (or `ruff format .`) before committing,
  otherwise `ruff format --check` in CI will fail on a separate step.
- Quickest pattern: `ruff check --fix . && ruff format .`

### 2026-04-03 — Know which formatter each repo uses before reformatting

- Some repos use `black` for CI formatting checks, others use `ruff format`. They are
  mostly compatible but differ on edge cases (e.g. trailing commas, magic trailing comma).
- Before running a formatter on a repo, check its CI step to determine which formatter
  it expects: `black --check` or `ruff format --check`.
- Never run `ruff format` on a repo whose CI uses `black --check` unless you also confirm
  the output is black-compatible (run `black --check` to verify after).
- Multi-line `run:` blocks in GitHub Actions YAML **require** the `|` block scalar indicator.
  A bare `run: first-command\n  second-command` is NOT two commands — it concatenates into one,
  causing mysterious "package not found" pip errors. Always use `run: |` for multi-step installs.

### 2026-04-04 — ruff line-length belongs in [tool.ruff], not [tool.ruff.format]

- Ruff rejects `line-length` in `[tool.ruff.format]` — it must live in the parent `[tool.ruff]`
  table. The `[tool.ruff.format]` subtable accepts `quote-style` and `indent-style` only.
  Confirmed in ruff 0.15.9. Do not put line-length in [tool.ruff.format] regardless of what
  documentation or instructions say — always verify against installed ruff version.
- When standardizing formatter config, always put `line-length = 100` under `[tool.ruff]` and
  keep `[tool.ruff.format]` limited to `quote-style = "double"` and `indent-style = "space"`.

### 2026-04-04 — black and ruff format can genuinely disagree on some constructs

- At `line-length = 88`, black and ruff format agree on the vast majority of code, but genuine
  incompatibilities exist: multiline `if`-condition wrapping, magic trailing comma handling,
  and certain lambda / inline comment edge cases.
- When both formatters must coexist during a transition period, make the deprecated formatter
  (`black`) non-fatal in CI (`|| true` or `|| echo "NOTE: ..."`) so the primary formatter
  (`ruff format`) is the enforcing check.
- For per-site circular disagreements: use `# fmt: skip` on the offending line or extract the
  problematic construct to a named variable — both formatters then skip or agree on the result.
- `# noqa` comments that exist solely to suppress lint rules not in the repo's selected ruff
  rule set can be safely removed when they are the sole cause of a formatter conflict.

### 2026-04-04 — Portfolio-wide formatter standardization pattern

- Standard config for every Python repo: `[tool.ruff]` with `line-length = 100`,
  `[tool.ruff.format]` with `quote-style = "double"` and `indent-style = "space"`,
  and `[tool.black]` with `line-length = 100` and repo-appropriate `target-version`.
- Standard `[tool.ruff.lint]`: `select = ["E","F","I","UP","B","SIM"]`, `ignore = ["E501","B008","SIM108"]`.
  Add `SIM117` (nested with) to ignore for test-heavy repos where that pattern aids readability.
- Pre-commit ordering: `ruff-format` (preferred) appears BEFORE `black` (deprecated).
- CI ordering: `ruff format --check .` (primary enforcing step) BEFORE `black --check --diff .`
  (deprecated, non-fatal if genuine incompatibility exists).
- Always run `ruff check --fix --unsafe-fixes .` then `ruff format .` then `black .` in that order.
- For repos without pyproject.toml (personal-finance), add `--line-length 100` flags to all
  ruff and black CI commands instead of creating a pyproject.toml.

### 2026-04-XX — SIM117 is commonly noisy in test-heavy repos

- `SIM117` warns about nested `with` statements that could be combined into one.
- In test code, nested `with` statements (e.g. `with patch(...):` inside `with pytest.raises():`)
  are often intentionally separate for readability and should not be auto-combined.
- Add `SIM117` to `ignore` in `[tool.ruff.lint]` for repos where nested `with` is a common
  test pattern rather than trying to fix each instance individually.

### 2026-04-XX — Portfolio-wide lint select is now E/F/I/UP/B/SIM at line-length=100

- All Python repos now have `[tool.ruff.lint]` with `select = ["E","F","I","UP","B","SIM"]`
  and `line-length = 100` in `[tool.ruff]` and `[tool.black]`.
- ruff target-version is a string (`"py310"`); black target-version is a list (`["py310"]`).
- sonetsim keeps `target-version = "py38"` (ruff) and `["py38","py39","py310"]` (black) due to
  min Python 3.8 requirement; UP fixes were safe at py38 for this repo.
- citegres has one remaining E722 (bare except) in legacy code that is not auto-fixable.

### 2026-04-03 — Inline `||` with quoted colon in GitHub Actions `run:` breaks YAML

- A one-line `run: command || echo "message: with colon"` is invalid YAML — the `:` inside
  the double-quoted string causes a YAML mapping-values-not-allowed parse error.
- The workflow fails with 0 jobs (nothing runs) and no diagnostic message.
- Fix: always use `run: |` block scalar when the command contains `||`, `&&`, or any string
  with a `:` that might be mistaken for a YAML key.
- Validate locally with `python3 -c "import yaml; yaml.safe_load(open(f).read())"` before pushing.

### 2026-04-03 — Both ruff format and black are now portfolio-wide with black deprecated

- All Python repos now run `ruff format --check` (primary) and `black --check` (deprecated)
  in CI and pre-commit.
- `[tool.ruff.format]` and `[tool.black]` with `line-length = 88` are in all `pyproject.toml` files.
- `# TODO: remove black once ruff format is confirmed stable portfolio-wide` marks black steps.
- `line-length` must go in `[tool.ruff]`, NOT `[tool.ruff.format]` — ruff rejects it in the
  format subtable.

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
