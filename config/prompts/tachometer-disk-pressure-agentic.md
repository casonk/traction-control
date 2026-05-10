You are running an unattended tachometer disk-pressure remediation task inside
`./util-repos/traction-control`.

Goal:
- For each clean candidate repo listed below, implement or repair reversible
  repo-local auto compression/decompression for local-only storage pressure.
- The trigger is tachometer disk pressure: open `system.disk` / `host.disk`
  backlog entries or summary disk utilization above the configured threshold.

Working rules:
- Read `AGENTS.md`, `LESSONSLEARNED.md`, and `CHATHISTORY.md` in
  `traction-control` first.
- Work only on repos listed as `candidate` in the appended inventory.
- Read each target repo's `AGENTS.md`, `LESSONSLEARNED.md`, and
  `CHATHISTORY.md` before editing.
- Before editing a candidate repo, confirm its worktree is still clean. If it
  is dirty, skip it and document why.
- Inspect the repo's high-volume local paths before choosing archive targets.
  Prefer local-only caches, generated artefacts, temporary downloads, browser
  profiles, build outputs, and debug snapshots.
- Do not target tracked source files, credentials, raw private data, account
  data, or irreplaceable user inputs unless the repo already has a clearly safe
  restore path for that exact data class.
- Use the `personal-finance` storage archive implementation as the reference
  shape when appropriate: `config/storage_archives.json`,
  `scripts/manage_storage_archives.py`, `.storage-archives/` in `.gitignore`,
  auditable run records, explicit `status`, `compress`, `decompress`, and
  threshold-based `auto` commands, plus scheduler restore-before-run and
  compress-after-run hooks where the repo has scheduled workflows.
- Keep implementations conservative and repo-specific. Do not add a generic
  destructive cleanup job.
- If a repo already has suitable archive automation, verify it and update docs
  or tests only if needed.
- Add focused tests for archive/restore behavior when the repo has a test
  suite. For script-only repos, run shell syntax checks plus a dry-run/status
  check.
- Update `README.md` or the relevant operator docs with manual restore and
  status commands.
- Update local-only `CHATHISTORY.md` in each modified target repo and in
  `traction-control`; add durable lessons only when they generalize.
- Audit staged diffs for secrets, local-only personal identifiers, account
  numbers, and raw data before committing.
- Run the target repo's local verification gate from `AGENTS.md` before commit.
- Commit and push each modified target repo only after local validation passes.
  After each push, verify hosted CI when the repo has GitHub Actions.
- Do not rewrite history or revert unrelated user changes.

Outcome:
- Keep the final summary concise and operational: repos changed, archive
  targets added, validation results, commits, pushes, and CI status.
- Proceed autonomously and do not wait for user input.
