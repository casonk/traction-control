# BACKLOG.md

Portfolio backlog for this repository. Pending items are candidates for execution —
manually or via crew-chief. Entries sourced from archility audit are tagged
`[archility:YYYY-MM-DD]`; manual entries use `[manual:YYYY-MM-DD]`.

The archility twice-weekly job populates this file automatically via `archility audit --write-backlog`.
To execute a backlog item with crew-chief: `crew-chief agent "Work on item: <item text>"`.
Mark items `[x]` when complete and move them to Done.

## Pending

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

## In Progress

## Done

- [x] [manual:2026-06-11] Add stale-age archive rotation to
  `util-repos/fedora-debugg` for ignored `artifacts/snapshot-*` directories.
  Implemented reversible repo-local move rotation with a manifest and restore
  command, wired it into `run_workflow.sh`, and rotated 171 user-owned stale
  snapshots into `artifacts/archive/snapshots/`. Four `nobody:nobody` snapshots
  remain active because they need an ownership/elevated cleanup decision.
