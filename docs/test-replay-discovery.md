# Historical Test Replay Discovery

`traction-control` discovers candidates across the portfolio; `crew-chief`
reviews and benchmarks them. Discovery reuses tests added in existing PRs. It
does not invent assertions or assume that a PR's author used any particular
model.

## Discover and review

The default sweep reads the ignored, owner-only visibility registry and
portfolio catalog. It scans only active public checkouts whose GitHub remotes
match their registered identity:

```bash
python3 scripts/discover_test_replays.py --max-commits 100 --review
```

When the catalog has not been initialized, supply explicit public checkout
paths relative to the portfolio root. The registry and checkout remote are
still verified:

```bash
python3 scripts/discover_test_replays.py \
  --checkout util-repos/crew-chief \
  --checkout util-repos/archility \
  --max-commits 100 --review
```

For a wider local sweep without maintaining a path list, scan the portfolio
root for checkouts and verify each candidate against the public registry:

```bash
python3 scripts/discover_test_replays.py \
  --scan-public-checkouts --max-commits 300 --review --review-limit 100
```

The scan excludes `archive-repos/`, ignores unregistered checkouts, and skips
registered public checkouts whose remotes or repository state fail verification.
Those skips and their reasons appear in the ignored `index.json`; inspect them
before interpreting the sweep's coverage. Review may need local socket access
for suites that bind localhost ports. A socket permission failure is an
environment result, not a model score.

The script finds first-parent commits with a PR number in their subject and
changes to both `tests/test_*.py` and existing `src/*.py` files. It writes
draft crew-chief task manifests, an index, and an origin-model evidence ledger
under ignored `reports/test-replay-candidates/`. Every case begins
**unreviewed**. The draft test command is `pytest -q`; inspect it and the
editable paths for each repository.

`--review` calls crew-chief's historical preflight in disposable checkouts. A
case enters `reviewed-suite.json` only when its old suite passes, later tests
fail by assertion on old source, and its reference suite passes. Collection
errors alone are rejected. Review failures and test counts are recorded in
`review.json`. The review gives no source fix to a model.

Run the accepted suite from `crew-chief` with its normal benchmark command:

```bash
python3 scripts/test_benchmark.py \
  --suite ../traction-control/reports/test-replay-candidates/reviewed-suite.json \
  --portfolio-root ../.. \
  --models llama3.2:latest mistral:latest \
  --runs 3 --agent-timeout 120
```

The generated candidate and reviewed suites stay local. Promote an individual
case to a tracked crew-chief suite only after inspecting its test diff, prompt,
editable paths, runtime dependencies, and preflight result. The discovery
script does not change target repositories or commit benchmark reports.

## Original-model evidence

`origin-evidence.local.json` starts each case at `status: unknown` and
`model: null`. A later sweep preserves manual entries. Set `status: verified`
only with direct evidence tying the exact model and run to the source change
and passing new tests, such as a contemporaneous agent transcript and test
result. Include the model tag or digest, the tested commit, test command, and
an evidence reference. A green reference commit or hosted CI run establishes
that the final code passed tests; it does not identify which model produced
that code. A benchmark pass on a seeded regression is also separate from the
model that originally implemented a historical PR.

Keep session references and model-origin claims in the ignored owner-only
ledger. Do not add per-artifact model attribution to tracked PRs, commits,
source files, or generated documentation.
