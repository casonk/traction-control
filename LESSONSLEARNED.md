# LESSONSLEARNED.md — Portfolio Control Plane

> Purpose: record durable lessons that should change how future agents work across the portfolio.
> Unlike `CHATHISTORY.md`, this file is tracked and should keep only reusable lessons.

## How To Use This File

- Read this file before repeating setup, publishing, audit, or automation workflows.
- Add lessons that generalize beyond a single session.
- Keep entries concise and action-oriented.
- Do not use this file for transient status updates or full session logs.

## Lessons

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

### 2026-03-26 — New shared utility repos need control-plane and repo-AGENTS updates together

- When introducing a new portfolio utility repo, add it to the `traction-control` inventory and shared-utility documentation in the same change that creates the repo.
- Update the shared-utility snippet in repo-level `AGENTS.md` files at the same time so agents working inside other repos can discover the new implementation home immediately.

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
