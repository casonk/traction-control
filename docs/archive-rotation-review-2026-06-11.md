# Archive Rotation Review — 2026-06-11

Scope: portfolio-wide stale/generated file review from `./util-repos/traction-control`.
Repos were scanned from the portfolio root (`../..`) and `.git` directories were
excluded from size and stale-candidate checks.

## Review Criteria

Good archive-rotation candidates are repo-local, reversible, and low-risk:

- ignored generated outputs, reports, browser profiles, debug snapshots, temp
  downloads, and cache directories
- large enough or old enough to matter operationally
- restorable from source systems or from a verified `tar.gz` archive
- not tracked source, credentials, raw private inputs, account data, or
  irreplaceable user data

Tracked coursework PDFs, certification files, docs, and intentionally versioned
diagrams were not classified as stale even when old, because they are repository
content rather than cleanup candidates.

## Portfolio Findings

| Priority | Repo | Candidate | Finding | Recommended Automation |
|---|---|---:|---|---|
| P0 | `research-repos/zillow-public-data` | `data/` 3907.9 MiB, `.zillow-generated-archives/data.tar.gz` 1595.3 MiB | Repo already has reversible generated-output compression, but the restored source tree and archive both exist. | Run or schedule the repo's existing `compress_generated_artifacts.py auto` behavior after refreshes so restored generated data is pruned again when no longer needed. |
| P0 | `personal-finance` | `artifacts/` 1048.0 MiB | Repo already has configurable archive rotation for downloader runtime caches and debug outputs. Current status shows large browser/cache sources still present because disk use is below the configured auto high-watermark. | Add scheduler hooks around weekly/manual refreshes to run `manage_storage_archives.py auto`, or lower target-specific thresholds if the intent is stale-age cleanup independent of disk pressure. |
| P1 | `util-repos/fedora-debugg` | `artifacts/` 336.9 MiB | Ignored snapshot artifacts include many 2026-04/2026-05 snapshots. Worktree is dirty, so implementation should wait until current tachometer edits settle. | Add repo-local snapshot archive rotation: compress ignored `artifacts/snapshot-*` after 14-30 days, keep the newest N uncompressed, and retain a manifest for restore/audit. |
| P2 | `util-repos/intake` | `reports/` 3.5 MiB, `data/` 3.5 MiB | Small today, but reports are generated and will grow with receipt ingestion. `data/intake.db` should not be auto-pruned without a clear backup/restore story. | Consider report-only rotation when reports exceed a threshold; leave SQLite data out of generic stale cleanup. |
| P2 | `util-repos/snowbridge` | `reports/private-access-debug-*.log` | Old ignored debug logs exist but are tiny. | Optional log retention: prune or archive reports older than 60-90 days during existing diagnostics workflows. |
| P2 | `util-repos/tachometer` | `.tachometer/run-all.log` | Old ignored run log exists but is tiny. | Optional log rotation in tachometer's own profiling workflow. |
| No action | `doc-repos/university-coursework`, `doc-repos/Certifications`, `research-repos/citegres` | old PDFs | Files are tracked repo content. | Do not move to archive automation unless the repository owner decides to reorganize tracked historical content. |
| No action | `doc-repos/casonk.github.io` | ignored `private_data/sat.pdf`, `_site/private_data/sat.pdf` | Old ignored private/generated site data exists; size is small and path is sensitive. | Do not touch through generic cleanup; handle only with explicit site/privacy task. |

## Existing Coverage Confirmed

- `personal-finance/scripts/manage_storage_archives.py status` reports archive
  root `.storage-archives`, auto enabled, high watermark `75.0%`, low watermark
  `70.0%`, and configured targets for bank/credit/invest downloader caches,
  debug HTML, OAuth profiles, connector debug outputs, and score snapshots.
- `research-repos/zillow-public-data/scripts/compress_generated_artifacts.py status`
  reports `data` as both restored source and archived output, and `viz` below
  the current compression threshold.
- `traction-control/scripts/tachometer_disk_pressure_agentic.sh` already
  launches repo-local archive automation work when tachometer reports disk
  pressure, but that trigger is pressure-based, not stale-age-based.

## Recommended Next Steps

1. Add a stale-age archive-rotation backlog item to `fedora-debugg` after its
   current dirty worktree is resolved.
2. Add post-refresh `auto` hooks in `personal-finance` scheduled workflows so
   existing archive targets are compressed after successful runs, not only when
   disk pressure crosses the high watermark.
3. Decide whether `zillow-public-data` should prune restored `data/` immediately
   after refresh/report generation or keep source plus archive until the next
   disk-pressure event.
4. Keep generic automation conservative: archive ignored generated/runtime
   files only, never tracked docs or raw private/account data.
