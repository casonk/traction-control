# Repository Quality Gates

`scripts/check_repository_quality.py` checks the Git index: the files that
would enter the next commit. Stage new files before running it. It exits 0
for a valid repository, 1 for contract failures, and 2 for setup errors.

The `base` profile requires tracked governance and architecture source files,
including `.editorconfig`, `CHANGELOG.md`, and `REFS-PUBLIC.md`. It rejects
local session files and common runtime/build output, and rejects
PlantUML or draw.io renders whose source is not tracked. It does not forbid
all generated output: reviewed architecture renders are part of the portfolio
baseline, and some repositories deliberately track other artifacts.

The `code` profile also requires a pre-commit configuration, a tracked `tests/`
tree, and a tracked CI workflow. A repository opts into this profile after
checking that those requirements match its actual role. File extensions are
not a reliable classifier: documentation and operations repos also contain
scripts. The daily portfolio audit uses `base`; this repository's pre-commit
configuration uses `code`.

Repositories with an ignored artifact directory can add an exact prefix to
their hook. This repository forbids tracked `artifacts/`:

```yaml
- id: repository-quality-contract
  name: tracked repository structure and artifact contract
  entry: python3 scripts/check_repository_quality.py --profile code --forbid-prefix artifacts/
  language: system
  pass_filenames: false
  always_run: true
```

The checker uses only the Python standard library and Git. A repository can
copy the script and the hook when adopting the contract; `traction-control`
owns the policy and its tests. Do not invoke a sibling checkout by absolute
path from hosted CI.

The render check proves source pairing, not pixel or SVG freshness. Archility
owns rendering and architecture drift checks. Its scheduled audit/render job
remains the heavier check; a per-repo CI render comparison needs deterministic
tool versions and clean-worktree output before it can be a reliable gate.
