# Private Portfolio Sidecar

The private sidecar is a three-level data-protection system for repository
content that must not be committed to Git. It complements the ignored
portfolio catalog and visibility registry; it does not replace application
transactions, Git hosting, or the lifecycle review.

`scripts/portfolio_sidecar.py` is the executable schema-v1 coordinator. It is
deliberately standalone and single-writer. Mesh nodes are storage replicas,
not alternative coordinators, until a quorum authority can issue leases and
fencing tokens.

## Configuration Boundary

Tracked files contain only the schema examples:

- `config/portfolio-sidecar/policy.example.json`
- `config/portfolio-sidecar/targets.example.json`

Operational files should be named `policy.local.json` and
`targets.local.json`; the coordinator writes `state.local.json`. All three
remain ignored, use mode `0600`, and live in an owner-only directory. They may
contain private repository IDs, selectors, node identities, and paths to
credential files. Tracked examples use only synthetic identifiers, paths, and
private addresses.

The examples are documentation, not operational defaults. Bootstrap an empty
local pair from the current private/public registry before enrolling any data:

```bash
python3 scripts/portfolio_sidecar.py init-config \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --policy config/portfolio-sidecar/policy.local.json \
  --targets config/portfolio-sidecar/targets.local.json
```

`init-config` creates owner-only, ignored `policy.local.json` and
`targets.local.json` files bound to the registry ID and generation. Both local
documents start at generation `0`, with zero datasets and zero target sets, so
the result is deliberately inert. It does not create credential files,
provision a destination, or create `state.local.json`. The operator must
explicitly populate the selectors and target topology, provision the matching
credentials and remote repositories, advance both document generations to `1`,
and then run `init-state`.

When the control directory is inside a Git worktree, bootstrap accepts only a
tracked `.gitignore` rule unchanged from `HEAD`; global excludes,
`.git/info/exclude`, and an uncommitted rule do not qualify. The command also
keeps its ignored process lock as a single-linked owner-only file.

Creation is no-overwrite and rolls back ordinary failures, but two files cannot
be published with one filesystem transaction. If interruption or power loss
leaves exactly one local document, `init-config` fails closed instead of
overwriting it. Confirm that the lone file is the inert owner-only bootstrap
document, remove only that file, and rerun `init-config`; never delete a
populated policy or targets document as generic recovery.

The directory also ignores local credentials, spool, state, lock, and
temporary artifacts as an accidental-commit guard. `.gitignore` is not an
authorization, confidentiality, or encryption boundary: it does not stop
another process from reading a file, prevent an explicit `git add -f`, or
remove content already committed. Prefer an external secret store and keep
only absolute paths to separate owner-only credential files in the active
target config. Never put a password, repository URI, private key, recovery
key, token, or live credential in tracked config or Git. Required ignored
credential files are owner-only operational inputs and must never be Git-staged
or force-added.

The policy and targets documents bind to the same visibility-registry ID and
generation. Policy and target generations advance independently and are
recorded in ignored state. A stale or mismatched registry generation fails
closed instead of selecting a repository under old visibility assumptions.
Each dataset has an opaque dataset ID, immutable repository ID, tier, exact
selectors, the schema-v1 `filesystem-static` adapter, file and byte ceilings,
and one target-set ID.

Schema v1 accepts a dataset only when its immutable repository ID is already
classified public by the current registry. That registry observation records
the result of a separate publication review; the sidecar never authorizes a
private-to-public transition.

The state also records `policy_sha256` and `target_sha256`. The latter binds the
target topology and paths plus hashes of the current repository URI, restic
password, and SSH identity bytes; raw credentials are not written to state.
Changing policy content, target content, or any credential therefore makes the
existing state fail closed even if an operator forgets to advance a generation.
Schema v1 has no state migration or credential-rotation command. For an
intentional rotation, advance the applicable generation, retain the old state
as private audit material outside the active path, and run `init-state` to begin
a deliberately new state epoch before backing up again.

Each target supplies absolute `repository_file`, `password_file`, and
`identity_file` paths. The first secure file contains the live restic SFTP
repository URI, the second contains its password, and the third is an explicit
SSH private key selected for that target. None of their contents appears in JSON, logs,
or tracked examples. Each file must be a real owner-only file, separate from
the other two. Every SFTP repository URI must include an explicit safe
`user@host`; the coordinator never falls back to the local operating-system
username, including during a manual failover. A hosted target has a null
`mesh_address`. A mesh target has a distinct failure domain and an RFC 1918
IPv4 literal `mesh_address` that must exactly match the SFTP repository URI
host. Schema v1 accepts only scp-style SFTP repository URIs; IPv6 and custom
SSH ports remain unsupported until they have live Restic integration coverage.

That address check is a syntactic fail-closed restriction, not proof of
WireGuard membership or routing. Schema v1 does not consult an authoritative
peer inventory, inspect the active WireGuard configuration, or prove that
packets cannot take a non-mesh route. Operators must separately pin SSH host
keys and verify the peer and route before enabling a target. Binding targets to
an authoritative WireGuard inventory is future work.

Selection is explicit and fail-closed. Being ignored by Git does not enroll a
path. Selectors are exact normalized POSIX paths relative to the checkout, not
globs, regular expressions, or include/exclude rules. Every L2 or L3 source
must be bound to an immutable repository ID, remain Git-ignored, resolve
beneath the expected checkout, and not traverse a symlink. A selected
directory is a named root for the static-filesystem adapter; the adapter
recursively accepts only stable regular files beneath it. Tracked paths,
special files, selector overlap, limit overruns, or source/config changes
during capture fail closed.

Backup capture copies selected bytes into an ignored, owner-only local spool
before invoking restic. The staging copy is plaintext: directories are made
owner-only and staged files are frozen read-only for the owner, but this is not
at-rest encryption. Protect the backing disk accordingly. A normal run removes
its operation directory; an interrupted process may leave a private spool that
must be treated as sensitive and removed only after confirming no sidecar
process is using it.

## Operator Workflow

Install Git, restic, and OpenSSH locally. Pre-provision and initialize every
restic SFTP repository, account, dedicated SSH identity, and pinned
`known_hosts` entry before running the coordinator. The runtime never runs
`restic init`, creates remote accounts, installs WireGuard, or provisions SFTP
servers. Keep control and credential directories at mode `0700` and their files
at mode `0600`; the identity file specifically requires exact mode `0600`.

After `init-config` and explicit provisioning, the operational commands share
`--private`, `--public`, `--catalog`, `--portfolio-root`, `--policy`,
`--targets`, and `--state`. A typical local flow is:

```bash
sidecar_common=(
  --private /absolute/control/private.local.json
  --public /absolute/control/public.local.json
  --catalog /absolute/control/portfolio.local.json
  --portfolio-root /absolute/portfolio
  --policy /absolute/control/policy.local.json
  --targets /absolute/control/targets.local.json
  --state /absolute/control/state.local.json
)

python3 scripts/portfolio_sidecar.py init-state "${sidecar_common[@]}"
python3 scripts/portfolio_sidecar.py validate "${sidecar_common[@]}"
python3 scripts/portfolio_sidecar.py plan "${sidecar_common[@]}"
python3 scripts/portfolio_sidecar.py backup "${sidecar_common[@]}" \
  --restic /absolute/bin/restic \
  --ssh /absolute/bin/ssh \
  --known-hosts /absolute/control/known_hosts
```

`plan --show-paths` is an explicit disclosure option; plain `plan` keeps
selected private paths out of its output. `backup` is the only command that
accepts `--restic`, `--ssh`, and `--known-hosts`. A zero backup exit means every
configured target acknowledged its dataset. Exit 3 means at least one result
was partial or degraded; state advances only for datasets that still met their
acknowledgement threshold. Neither status is restore proof.

## Protection Levels

| Level | Data | Recovery path |
|---|---|---|
| L1 | Git-tracked code reviewed as public-safe | Normal Git history and its reviewed remote; the remote still starts private under the portfolio's private-first policy |
| L2 (`hosted-encrypted`) | Explicitly selected, Git-ignored private data | Restic encrypts on the client and writes one private hosted SFTP repository |
| L3 (`mesh-only`) | Explicitly selected, Git-ignored highest-sensitivity data | Restic encrypts on the client and writes independent repositories on at least three private-address SFTP endpoints intended for the WireGuard mesh |

L1 describes content suitability, not permission to publish. A repository
remains private until its separate history, dependency, secret, license, and
consumer review authorizes a public transition.

L2 target identity and SSH host keys must be pinned. The hosted service sees
only restic-encrypted repository data, but its availability and account
security remain part of the threat model.

L3 has no hosted fallback. Every member is configured with a private-address
SFTP endpoint intended to be reachable through WireGuard. The v1 coordinator
does not itself establish that endpoint's WireGuard membership or route.
Three members is the minimum; adding a member changes the fixed membership and
the strict-majority threshold rather than creating a best-effort destination
pool. Distinct failure domains must represent independently useful durability
boundaries, not aliases for the same disk or host.

## Snapshot And Durability Contract

Each committed dataset sequence records a stable capture-manifest hash and a
separate restic snapshot ID for each acknowledged target. Restic exit status
zero plus one syntactically valid snapshot ID is acknowledgement evidence
only. It is not a repository-integrity check, proof that the snapshot can be
restored, or proof that its restored bytes match the capture manifest. For an
L3 set of three nodes, two acknowledgements form a strict majority. The latest
sequence may be recorded as degraded at two of three. Fewer than a strict
majority is a failed backup. Missing members are never silently removed, and a
`mesh-only` job never redirects to the hosted target.

Executable v1 stores only the latest committed dataset state. It has no
append-only operation history and does not automatically repair a missing
replica from a prior degraded sequence. Operators must preserve the degraded
result and complete recovery work out of band; a future history and repair
subsystem must make that process explicit and resumable.

Majority durability is a backup-completion threshold, not multi-writer CRUD
conflict resolution and not proof that a coordinator was elected safely. The
application remains responsible for ACID behavior. A database selector must
consume an application-supported consistent export or hot-backup API; the
sidecar must never copy live database, journal, WAL, or consensus files and
claim the result is recoverable.

The executable v1 treats snapshots as append-only and immutable. It has no
`forget`, `prune`, or repository-deletion path. Capacity monitoring must
therefore assume unbounded growth until a separately reviewed retention
design exists. This is coordinator-enforced logical immutability, not SFTP
WORM storage: a compromised server account or administrator may still destroy
repository bytes. Server-side immutable snapshots may strengthen the target,
but do not relax the independent restore-drill requirement.

## Coordinator And Failover

The executable v1 has exactly one statically selected coordinator. It owns the
local operation spool and should be the only host provisioned with target write
credentials. A same-host process lock serializes commands, but it is not a
distributed lease or fencing token. Moving authority to
another host is a manual fenced handoff: stop the old coordinator, revoke or
disable all of its target identities, prove that it can no longer write, and
only then provision the replacement. If exclusive ownership cannot be proven,
do not start the replacement. Coordinator loss pauses backups; v1 does not
promote a nearby mesh node automatically.

Automatic failover is planned behind a private replicated-state authority
using rqlite/Raft. That authority must commit membership, coordinator leases,
monotonic fence epochs, dataset sequences, per-target acknowledgements, repair
state, and restore evidence through quorum before a new coordinator may act.
Network proximity may select the closest healthy request endpoint, but it
must never select the authoritative writer or bypass quorum.

## Restore Drills

Executable v1 does not implement `restic check`, restore commands, drill
evidence, repair, or backup history. Therefore its acknowledgement record alone
must never be presented as restore validation. The following is an operator
requirement and a contract for future automation, not behavior currently
performed by `portfolio_sidecar.py`.

A snapshot is not accepted as a backup solely because restic returned success.
On an operator-defined schedule, a drill must:

1. select an acknowledged immutable operation without changing retention;
2. authenticate and run repository integrity checks;
3. restore into a new isolated directory, never over the source;
4. validate hashes and the application's own consistency checks;
5. record the target, snapshot, result, duration, and policy, target, and state
   generations without disclosing private paths in tracked output; and
6. remove only the disposable restore directory after evidence is retained.

L3 drills restore the same logical operation independently from at least a
strict majority of targets. A failed target or mismatched restored manifest
makes the set degraded and requires out-of-band handling in v1; it must not
cause hosted fallback.

## Git History Caveat

Ignoring or untracking a path affects only future Git snapshots. Sensitive
content may remain in commits, tags, pull-request refs, forks, caches, and
existing clones. Moving data to L2 or L3 does not make a repository safe to
publish and does not sanitize history. If sensitive bytes were ever tracked,
stop publication, rotate exposed credentials, inspect all reachable history,
and handle any history rewrite as a separate destructive operation with a
verified backup and collaborator coordination.

See [the sidecar data-flow diagram](diagrams/private-sidecar.puml) and
[the portfolio lifecycle policy](portfolio-lifecycle.md).
