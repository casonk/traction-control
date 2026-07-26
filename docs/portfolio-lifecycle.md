# Portfolio Materialization And Lifecycle Review

`traction-control` is the master orchestrator for the portfolio. It is not a
Git superproject: it does not commit gitlinks, nest repository histories, or
make one repository commit determine every checkout. The ignored portfolio
catalog binds immutable GitHub repository IDs to local checkout paths, while
the ignored private/public registry remains the visibility authority.

These local control-plane files may disclose private repository names,
locations, and relationships. Keep them untracked, owner-only (`0600`), and
inside owner-only directories:

- `config/repository-visibility/private.local.json`
- `config/repository-visibility/public.local.json`
- `config/portfolio/portfolio.local.json`
- a lifecycle report such as `config/portfolio/lifecycle-review.local.json`

Tracked `*.example.json` files contain synthetic data only. Gitignore is an
accidental-commit guard, not encryption.

## Safe Materialization Boundary

The materializer creates missing checkouts and fetches remote objects for
existing, identity-matched checkouts. This provides a reproducible way to
clone the whole registered portfolio without turning it into one Git
repository.

This is a checkout orchestrator, not a complete GitHub backup. A normal clone
does not preserve issues, pull requests, Actions artifacts, releases, package
registries, repository settings, wikis, every LFS object, or other hosted
metadata. Backups and restore tests remain a separate lifecycle requirement.

Materialization is clone/fetch-only. It does not intentionally run repository
hooks or tracked programs: Git runs with system/global config disabled, an
empty hook path, filesystem monitoring disabled, and recursive submodules
disabled. It does not pull, merge, rebase, reset, clean, stash, prune, or push.
A dirty working tree is left untouched. An unknown identity, path collision,
symlink, shallow/sparse/partial checkout, or unclassified destination fails
closed for review. SSH is the default and recommended clone protocol for
private repositories because the hardened Git environment does not inherit
ambient HTTPS credential helpers. HTTPS therefore requires an explicit
non-global credential mechanism.

Seed the catalog from the existing workspace. Checkouts already bound to one
registered GitHub identity retain their paths; repositories not present
locally receive a deterministic `github/OWNER/REPO` proposed path:

```bash
python3 scripts/portfolio_materializer.py init \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..
```

Review the proposed `clone`, `fetch`, `manual`, and `absent` operations before
materializing anything:

```bash
python3 scripts/portfolio_materializer.py plan \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..
```

By default, plan output hides private checkout paths, private slugs, and
unmanaged local paths. Use `--show-slugs` only in a controlled local terminal
when full private metadata is required for path review.

Immediately after `init`, observe GitHub archive state before the first hosted
plan. `init` deliberately starts entries as active:

```bash
python3 scripts/portfolio_materializer.py refresh \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..
```

When private creation or a reviewed visibility/rename reconciliation advances
the registry, preserve existing catalog paths and policies while adding new
immutable IDs:

```bash
python3 scripts/portfolio_materializer.py reconcile \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..
```

`reconcile` is local-only, locked, and additive. It refuses IDs removed from
the registry; removal needs an explicit lifecycle/tombstone design rather
than silent lineage loss.

After reviewing paths and storage impact, `materialize` clones only missing
desired checkouts. `sync` fetches `origin` only for clean `fetch-only`
checkouts. `audit` performs no writes:

```bash
python3 scripts/portfolio_materializer.py materialize \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..

python3 scripts/portfolio_materializer.py sync \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..

python3 scripts/portfolio_materializer.py audit \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../..
```

Hosted checks are the default. `--skip-github` exists only for controlled
offline tests and does not establish identity or visibility.

Local Git repositories with no registered GitHub identity are not silently
discarded or uploaded. They appear as `review-unmanaged` items and lifecycle
coverage blockers until the owner creates a private remote through the
private-first workflow or completes a separately reviewed backup/retirement
process. Schema version 1 does not yet have an accepted local-only
identity/disposition ledger, so “keep local” cannot be marked complete and
remains a visible blocker rather than a silent exclusion.

## Read-Only Lifecycle Evidence

Generate a local report after validating the visibility registry and
portfolio catalog:

```bash
umask 077
python3 scripts/portfolio_lifecycle_review.py \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../.. \
  --output config/portfolio/lifecycle-review.local.json
```

The reviewer lists tracked dependency manifests and tracked text references
to registered `OWNER/REPO` slugs. Evidence records use immutable source and
target repository IDs, the source commit when one is available, and the
tracked file and line number. Evidence bytes come from verified `HEAD` Git
objects; raw working-tree bytes are hashed only to prove they still match that
commit. Repository content is never imported, sourced, built, installed, or
executed.

Tracked staged or unstaged changes block scanning before any working-tree
bytes are attributed to `HEAD`; assume-unchanged, skip-worktree, unmerged, and
non-HEAD index state also fail closed. Git inspection disables ambient and
repository-configured hooks, filters, fsmonitor, replacement objects,
alternates, lazy fetch, and recursive submodules. Untracked files are outside
this evidence model. Common Git dependency URLs ending in `.git` are recognized. Any
tracked reference from a public source repository to a private target is an
explicit publication and access blocker, not merely informational evidence.

Large files, binary files, tracked symlinks, missing tracked files, sparse
worktrees, shallow clones, partial clones, missing `HEAD` commits, and
unavailable checkouts are reported as coverage warnings or blockers. A clean
report is useful evidence, not proof that no runtime, generated, external, or
historical dependency exists.

The output contains private portfolio metadata. The command writes it with
mode `0600` and refuses an output path that is tracked or not ignored when it
is inside a Git worktree.

## Proposed Actions, Never Automatic Changes

Pass a plan to evaluate proposed lifecycle changes:

```bash
python3 scripts/portfolio_lifecycle_review.py \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../.. \
  --plan config/portfolio/lifecycle-review-plan.local.json \
  --output config/portfolio/lifecycle-review.local.json
```

Plan schema version 1 has exactly `schema_version` and `actions` at its root.
Every action has exactly:

- `action`: `make-private`, `archive`, `retire`, or `remove-dependency`
- `target_repository_id`: an immutable ID present in the registry
- `reason`: a nonempty, trimmed explanation
- `dependency_repository_id`: required only for `remove-dependency`, and also
  an immutable registered ID

The reviewer annotates the report with blockers and warnings. It never changes
GitHub visibility, archives or deletes a GitHub repository, edits a checkout,
or runs a Git mutation command.

After a reviewed public-to-private or rename operation is completed manually
on GitHub, use the visibility registry's `reconcile-observed` command to
record the exact external result locally. That command observes and records;
it never performs the GitHub change.

## Review Sequence

Use an evidence-preserving sequence:

1. Inventory incoming and outgoing dependency evidence, owners, releases,
   deployment consumers, secrets, and backups.
2. Remove or replace dependencies and verify downstream behavior.
3. Change a public repository to private manually only after access,
   publication, and consumer impact review.
4. Archive it manually when write access and active development should stop.
5. Keep a defined cooling period and verify a restorable backup.
6. Delete manually only after a separate, explicit approval and final
   recovery test.

Catalog schema version 1 intentionally has no deletion tombstone carrying
approval and backup evidence. Until that lineage-preserving model exists,
remove no immutable ID from the registry/catalog after deletion; the tools
fail closed instead of pretending a deleted repository never existed.

Making a GitHub repository public-to-private can break unauthenticated clones,
fork relationships, Pages, Actions consumers, package or release downloads,
badges, submodules, deployment keys, and third-party integrations. GitHub
also warns that public forks are detached rather than made private, and that
stars and watchers are erased. Review
[GitHub's current visibility consequences](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
and the repository's actual consumers immediately before the manual change.

`retire` describes the end of supported use; `archive` is GitHub's read-only
repository state. Neither means deletion, and this tool performs neither.

## Consistency Model

The current workflow is a fail-closed saga: local catalog/registry validation,
Git fetches, dependency cleanup, GitHub visibility, archival, backup, and
eventual deletion are separate effects with explicit reconciliation. They are
not one ACID transaction, and choosing the nearest mesh node must not create a
second writer.

Local materialization operations acquire the visibility-registry lock before
the catalog lock and compare the complete paired registry snapshot at the
operation boundary. A concurrent membership, slug, visibility, or generation
change fails closed instead of completing against stale authority.

The planned quorum-backed authority will place lifecycle intent, immutable
identity, approvals, leases, and outbox state behind transactional
consensus. GitHub and Git remain external systems, so even then their
effects require idempotent workers, verification, and compensation rather
than a claim of cross-system ACID atomicity.

Nearest-node reads will require an explicit linearizable/quorum consistency
mode or a bounded-staleness lease. WireGuard selects a protected transport
path; it does not select the authoritative writer. The interim JSON generation
check detects torn pairs but has no signed highest-seen checkpoint, so copying
an older coherent pair is not rollback-safe.
