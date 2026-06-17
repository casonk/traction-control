# BACKLOG.md

Portfolio backlog for this repository. Pending items are candidates for execution —
manually or via crew-chief. Entries sourced from archility audit are tagged
`[archility:YYYY-MM-DD]`; manual entries use `[manual:YYYY-MM-DD]`.

The archility twice-weekly job populates this file automatically via `archility audit --write-backlog`.
To execute a backlog item with crew-chief: `crew-chief agent "Work on item: <item text>"`.
Mark items `[x]` when complete and move them to Done.

## Pending

### Reusable Workflow Migration

- [ ] [manual:2026-06-17] **Tier 1** — Migrate 9 repos to reusable workflow callers (no blockers):
  tradility, locility, session-control, crew-chief, magneto, bit-byte-block → `python-ci.yml`;
  casonk.github.io → `docs-ci.yml`; fedora-debugg, terminility → `shell-ci.yml`.
  Also add `ruff` lint hook to tradility pre-commit (only Tier 1 repo missing it).

- [ ] [manual:2026-06-17] **Tier 2** — Add `skip-install` input to `python-ci.yml` reusable workflow,
  then migrate 5 pre-commit-only repos: traction-control, pit-box, short-circuit, snowbridge,
  wiring-harness. Add ruff check to pre-commit configs where applicable before migrating.

- [ ] [manual:2026-06-17] **Tier 3** — Fix pyproject.toml `[dev]` extras then migrate 5 repos:
  `dyno-lab` (add ruff-format to [dev]); `nordility` (switch CI to pip install -e ".[dev]");
  `tachometer` (fix dyno-lab reference from PyPI name to git URL in [dev]);
  `citegres` (add networkx/matplotlib to [dev]); `zillow-public-data` (add deps to pyproject,
  handle PYTHONPATH). Add ruff check pre-commit hook to each before migrating.

- [ ] [manual:2026-06-17] **Tier 5 — Per-repo decisions** (handle one at a time when ready):
  archility (unittest→pytest decision, PYTHONPATH=src, smoke test),
  auto-pass (smoke test, pinned tool versions),
  clockwork (drop redundant direct lint steps, then migrate),
  shock-relay (hybrid Python compile + ShellCheck — needs custom inline or new input),
  intake (pytest --tb=short → add pytest-args input, consolidate 2 jobs),
  doseido (add tachometer to [dev] extras, consolidate 2 jobs),
  fred-public-data (switch from requirements.txt to pyproject-based install),
  windshield (add ruff+pylint to [dev], add ruff hook to pre-commit),
  personal-finance (blocked on windshield being installable without GH_PRIVATE_REPO_PAT).

- [ ] [manual:2026-06-17] **Tier 6 — Major overhaul first** (handle when touching these repos):
  `sonetsim` — drop Python 3.8/3.9 (EOL), add [dev] extras, fix non-standard test paths;
  `pushshift_python` — resolve MPLCONFIGDIR env var need (conftest.py or new workflow input).

- [ ] [manual:2026-06-17] **Publish workflow migration** — Migrate `python-publish.yml` inline
  workflows to `casonk/.github/.github/workflows/python-publish.yml@main` for repos that have them:
  crew-chief, archility, auto-pass, clockwork, dyno-lab, nordility, tachometer, sonetsim.

- [ ] [manual:2026-06-17] **Secret-scan workflow migration** — Migrate `secret-scan.yml` inline
  workflows to `casonk/.github/.github/workflows/secret-scan.yml@main` across the portfolio
  after confirming each repo has `.gitleaks.toml` and `.gitleaks-baseline.json` in place.

- [ ] [manual:2026-06-17] **locility private repo** — Create private GitHub repo for locility
  (`gh repo create casonk/locility --private`), set SSH remote, push pending commits.
  Blocked until then: Tier 1 CI migration commit is local-only for this repo.

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

- [ ] [manual:2026-06-11] Add post-refresh archive hooks for
  `personal-finance` so existing `scripts/manage_storage_archives.py auto`
  coverage runs after successful scheduled/manual data refreshes, not only when
  disk pressure crosses the configured high watermark.
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

- [x] [manual:2026-06-11] Add stale-age archive rotation to
  `util-repos/fedora-debugg` for ignored `artifacts/snapshot-*` directories.
  Implemented reversible repo-local move rotation with a manifest and restore
  command, wired it into `run_workflow.sh`, and rotated 171 user-owned stale
  snapshots into `artifacts/archive/snapshots/`. Four `nobody:nobody` snapshots
  remain active because they need an ownership/elevated cleanup decision.
