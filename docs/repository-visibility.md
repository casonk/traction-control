# Repository Visibility Registry

`traction-control` owns the portfolio's repository-visibility classification.
Its authority is one paired, local registry:

- `config/repository-visibility/private.local.json`
- `config/repository-visibility/public.local.json`

Both files are ignored by Git. The tracked
[`private.example.json`](../config/repository-visibility/private.example.json)
and
[`public.example.json`](../config/repository-visibility/public.example.json)
contain synthetic identifiers only and document schema version 1.
The registry tool also refuses an active file that is tracked or is inside a
Git worktree without an effective ignore rule. Paths outside a worktree are
allowed for a more private XDG-style configuration location.

Other repositories may consume classification decisions through
`scripts/repository_visibility.py` or the private-repository creation wrapper.
They must not maintain duplicate authoritative lists. Copies inevitably become
stale and cannot safely resolve conflicts.

The separate ignored portfolio catalog maps immutable registry IDs to local
checkout paths without duplicating slugs or visibility. See
[Portfolio Materialization And Lifecycle Review](portfolio-lifecycle.md).

## Security Boundary

The local files can disclose the existence and names of private repositories.
Create them with mode `0600`, keep their parent directory accessible only to
the intended account, and protect workstation backups and storage. Repository
IDs are identifiers, not credentials, but they are still private metadata.

Gitignore is an accidental-commit guard, not a secrecy mechanism. It does not
encrypt files, restrict other local users, remove an already tracked file, or
protect copies made by logs, backups, synchronization tools, or processes.
Never place GitHub tokens, SSH keys, or other credentials in either registry.

## Paired Registry Invariant

Each document has these exact root keys:

| Key | Meaning |
|---|---|
| `schema_version` | Integer format version; currently `1` |
| `registry_id` | Stable identifier for this paired registry |
| `generation` | Monotonically increasing version of the pair |
| `visibility` | Exactly `private` or `public`, matching the file |
| `repositories` | Entries with an immutable GitHub node `id` and canonical `OWNER/REPO` `slug` |

The private and public files must have the same `schema_version`,
`registry_id`, and `generation`. An ID or slug may occur at most once across
the pair. Both documents advance to the same generation whenever an entry is
added. Partial writes, mismatched generations, duplicate identities, unknown
keys, malformed entries, or incorrect per-file visibility invalidate the
whole pair.

Slugs are useful labels; immutable GitHub node IDs anchor identity across a
rename. Entries are sorted by case-folded slug and then ID to keep changes
deterministic.

Mutating commands serialize local writers with the ignored, mode-`0600`
`.repository-visibility.lock` file. That lock coordinates processes on one
filesystem; it is not a distributed lock or a substitute for the paired
generation check.

## Initial Setup

Run setup from the `traction-control` repository:

```bash
umask 077
chmod 0700 config/repository-visibility
python3 scripts/repository_visibility.py init \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --registry-id portfolio-local
chmod 0600 \
  config/repository-visibility/private.local.json \
  config/repository-visibility/public.local.json
python3 scripts/repository_visibility.py validate \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json
```

`init` creates an empty generation-zero pair. Choose one durable
`registry_id`; do not give different nodes independently created registries
the same authority.

The examples are safe format references, but their fake entries are not an
inventory. Do not treat them as operational configuration. Because tracked
files may be restored as mode `0644`, the CLI intentionally rejects the
examples themselves as operational input; `init` creates mode-`0600` local
files.

## CLI Operations

Every command receives both paths so no decision can be made from half of the
registry:

```bash
python3 scripts/repository_visibility.py validate \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json

python3 scripts/repository_visibility.py classify \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --slug example-owner/example-repository

python3 scripts/repository_visibility.py record-private \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --id R_IMMUTABLE_GITHUB_NODE_ID \
  --slug example-owner/example-repository
```

`classify` prints exactly `private`, `public`, or `unclassified`.
`record-private` is idempotent for an exact existing private entry, refuses
all conflicts with public entries, and increments the generation in both
files for a new entry.

After an explicitly reviewed GitHub rename or visibility change has already
been performed manually, reconcile that observed external fact into the local
pair with exact before/after expectations:

```bash
python3 scripts/repository_visibility.py reconcile-observed \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --id R_IMMUTABLE_GITHUB_NODE_ID \
  --from-slug example-owner/old-name \
  --from-visibility public \
  --to-slug example-owner/new-name \
  --to-visibility private
```

`reconcile-observed` pins the observation to `github.com`, verifies the exact
immutable ID, canonical target slug, and target visibility, then locks and
compares the local source state before advancing both registry files. It
never changes GitHub. A rename and visibility change may be recorded together;
unexpected source state, target collisions, or remote drift fail without a
local mutation.

The private-creation wrapper uses these defaults:

```text
config/repository-visibility/private.local.json
config/repository-visibility/public.local.json
```

Set `TRACTION_CONTROL_PRIVATE_REPOS_CONFIG` and
`TRACTION_CONTROL_PUBLIC_REPOS_CONFIG` together to use another pair. The
override files must either be outside a Git worktree or be both ignored and
untracked; pointing at an ordinary stageable JSON file is rejected. The
wrapper creates unclassified repositories as private, verifies GitHub's
immutable ID and private visibility, and only then records the private entry.
It provides no publication path.

## Fail-Closed Decisions

| Observed state | Classification or action |
|---|---|
| Exact entry in the valid private registry | `private`; private-only operations may continue |
| Exact entry in the valid public registry | `public`; private-creation wrapper stops |
| No matching ID or slug in a valid pair | `unclassified`; creation is permitted only as private, followed by verified private registration |
| Slug and ID resolve to different entries | Stop; repair the registry before any remote change |
| ID or slug appears in both files | Stop; visibility is ambiguous |
| File missing, unreadable, malformed, or not a regular file | Stop |
| Schema, registry ID, generation, or per-file visibility mismatch | Stop |
| Remote lookup fails or returns incomplete identity data | Audit fails; make no visibility inference or remote change |
| Remote identity or visibility differs from its registry entry | Audit fails; investigate drift before another operation |

A public entry never authorizes publication. It records that a separate,
explicitly reviewed publication already occurred. Neither `classify`,
`record-private`, `audit`, nor `create_private_github_repo.sh` changes a
repository from private to public. Publication requires its own human-reviewed
history, secret, reference, licensing, and security-boundary audit followed
by an explicit GitHub visibility change. Only after remote verification may
the central registry be updated to describe that external fact.

## Remote And Portfolio Audit

Validate the local pair and compare every entry with GitHub:

```bash
python3 scripts/repository_visibility.py audit \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json
```

The audit uses `gh` to compare each stored node ID, slug, and visibility with
the complete repository inventory for every owner inferred from the registry.
GitHub CLI observations are explicitly pinned to `github.com`; an ambient
enterprise `GH_HOST` cannot redirect an audit for `github.com` Git remotes.
The private-first creation wrapper pins both creation and verification to the
same host. Canonical checkout identities accept authenticated SSH or HTTPS
GitHub transports; plaintext HTTP and unauthenticated `git://` remotes fail
closed.
This also detects repositories created outside the wrapper. Authentication
failure, missing or unregistered repositories, truncated owner inventory,
renames, ID mismatches, and visibility drift produce a failing result. For an
empty registry, or to audit another managed namespace explicitly, repeat
`--owner OWNER`.

Local portfolio roots can be checked for Git remotes whose GitHub slugs are
not classified:

```bash
python3 scripts/repository_visibility.py audit \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --portfolio-root ../..
```

Repeat `--portfolio-root` when auditing multiple workspaces. Use
`--skip-github` only for an intentionally local inventory check; it is not
evidence that remote visibility is correct. `--gh PATH` can select a
controlled GitHub CLI executable for testing or automation. Local checks
inspect every configured remote's fetch and push URLs, including separate
push URLs; a classified fetch origin cannot conceal an unclassified push
destination. A single checkout also fails when its configured URLs resolve to
more than one registered repository identity.

This local check is an inventory-consistency guard, not a checkout identity
proof or permission to push. A checkout whose only remote points to the wrong
but already classified repository cannot be distinguished without a separate
expected-identity binding. Verify the intended destination independently
before adding a remote or pushing. The private-creation wrapper deliberately
does neither.

Before committing or publishing a public repository, scan its staged Git
index for names or slugs classified private:

```bash
python3 scripts/repository_visibility.py audit-private-disclosures \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --root .
```

The audit reads index blobs, not uncommitted working-tree bytes, and reports
only sanitized file paths and counts. It also checks indexed pathnames,
including gitlink paths. The local pre-commit gate runs this command whenever
the active registry is available and always rejects staged operational
`*.local.*` control-plane files. A passing current-index audit does not cleanse
older Git history; history rewriting remains a separate destructive review.

## Replication And Future Authority

For now, the paired JSON files are a single-writer control-plane artifact.
Distribute them over an authenticated, encrypted channel, preserve `0600`,
and validate the matching generation before use on another node. Never merge
independently edited copies or select the nearest node's version by latency.
Generation equality detects a torn pair but does not provide a signed
highest-seen checkpoint, so copying an older coherent pair can still roll state
back. Do not describe file replication as rollback-safe.

The planned successor is a quorum-backed registry service. It will make
classification changes ACID transactions, use consensus for one committed
generation, and provide explicit leader failover between nodes. Until that
authority exists and consumers migrate to it, these local files remain
canonical and fail closed when their state cannot be proven coherent.
Nearest-node reads will be safe only through an explicit linearizable/quorum
read mode or a bounded-staleness lease. WireGuard proximity is transport, not
authority.
