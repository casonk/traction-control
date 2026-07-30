# Private Portfolio Sidecar

The private sidecar is a three-level data-protection system for repository
content that must not be committed to Git. It complements the ignored
portfolio catalog and visibility registry; it does not replace application
transactions, Git hosting, or the lifecycle review.

`scripts/portfolio_sidecar.py` is the executable coordinator. Policy and target
configuration remain schema v1; committed state is schema v2 and binds the
portable manifest stored inside every encrypted snapshot. The coordinator is
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

Before editing the inert policy, create a derived advisory inventory of
possible selectors:

```bash
python3 scripts/portfolio_sidecar.py inventory-candidates \
  --private config/repository-visibility/private.local.json \
  --public config/repository-visibility/public.local.json \
  --catalog config/portfolio/portfolio.local.json \
  --portfolio-root ../.. \
  --output config/portfolio-sidecar/inventory.local.json
```

The command locks and revalidates the visibility registry and portfolio
catalog, then inspects only registry-public entries whose desired presence is
`checkout`. It writes an owner-only, ignored JSON document bound to the current
registry and catalog generations. Refresh replaces that derived document
atomically while holding the same control-directory lock used by the other
sidecar commands. It never modifies policy, targets, state, or candidate data.

Candidate discovery is metadata-only: hardened Git commands enumerate ignored
names, while `lstat` and directory enumeration establish file kind, owner,
link count, file count, and total apparent bytes. Candidate file contents are
never opened. The inventory omits active sidecar controls, locks and temporary
files, tool caches, and common build outputs. It records exclusion counts by
reason. A missing desired checkout is recorded as `missing`; a dirty,
identity-mismatched, unsafe, or otherwise unverifiable checkout is recorded as
`unready` without candidate names.

Default terminal output contains counts only. `--show-paths` is an explicit
disclosure option for printing repository IDs and candidate selectors. The
ignored JSON necessarily contains those identifiers and selectors, which is
why it has the same owner-only handling requirements as policy and targets.
The document is a review aid, not enrollment authority: no candidate is
protected until an operator deliberately places exact selectors and limits in
`policy.local.json` and completes target provisioning.

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

State schema v2 also declares `manifest_format: portable-files-v1` and records
`policy_sha256` and `target_sha256`. The latter binds the
target topology and paths plus hashes of the current repository URI, restic
password, and SSH identity bytes; raw credentials are not written to state.
Changing policy content, target content, or any credential therefore makes the
existing state fail closed even if an operator forgets to advance a generation.
State v1 had no portable restore manifest and is explicitly refused; it is not
silently upgraded into apparent restore evidence. For an intentional rotation
or migration, advance the applicable generation, retain the old state as
private audit material outside the active path, and run `init-state` to begin a
deliberately new schema-v2 state epoch before backing up again.

Each target supplies absolute `repository_file`, `password_file`, and
`identity_file` paths plus an explicit integer `sftp_port` from 1 through
65535. The first secure file contains the live restic SFTP repository URI, the
second contains its password, and the third is an explicit SSH private key
selected for that target. None of their contents appears in JSON, logs, or
tracked examples. Each file must be a real owner-only file, separate from the
other two. Every SFTP repository URI must include an explicit safe
`user@host`; the coordinator never falls back to the local operating-system
username, including during a manual failover. The hardened SSH command passes
the selected port directly with `-p`. For compatibility, an older schema-v1
target entry that omits `sftp_port` is interpreted and canonically bound as
port 22; new and reviewed target entries should always spell the port out. A
hosted target has a null `mesh_address`. A mesh target has a distinct failure
domain and an RFC 1918 IPv4 literal `mesh_address` that must exactly match the
SFTP repository URI host. Schema v1 accepts only scp-style SFTP repository
URIs; IPv6 remains unsupported.

For a non-default port, pin the server key in `known_hosts` under OpenSSH's
`[host]:port` name rather than the unbracketed host name. The normalized port
is part of `target_sha256`. Therefore, a state document created by a runtime
from before explicit-port support intentionally fails its target binding even
when an old target entry omits the port. Archive that state as private audit
material, advance the target generation, and run `init-state` for a reviewed
new epoch; do not weaken the binding to preserve the old hash.

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
before invoking restic. User payload is placed below the reserved
`.portfolio-sidecar/payload/` namespace and the snapshot also contains the
canonical `.portfolio-sidecar/manifest.json`. The manifest identifies the
dataset and repository and records each exact source path, byte size, original
mode, and SHA-256 digest. It deliberately excludes host-specific inode, device,
UID, GID, and timestamp values. The state `manifest_sha256` binds these portable
manifest bytes.

The staging copy and a drill restore are plaintext: directories are made
owner-only and staged files are frozen read-only for the owner, but this is not
at-rest encryption. Protect the backing disk accordingly. A normal run removes
its operation directory; an interrupted process may leave a private spool that
must be treated as sensitive and removed only after confirming no sidecar
process is using it. Selectors colliding with the reserved internal namespace
are refused.

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
python3 scripts/portfolio_sidecar.py drill "${sidecar_common[@]}" \
  --restic /absolute/bin/restic \
  --ssh /absolute/bin/ssh \
  --known-hosts /absolute/control/known_hosts \
  --evidence /absolute/control/drill-2026-07-27.local.json
```

`plan --show-paths` is an explicit disclosure option; plain `plan` keeps
selected private paths out of its output. `backup` and `drill` accept
`--restic`, `--ssh`, and `--known-hosts`. A zero backup exit means every
configured target acknowledged its dataset. Exit 3 means at least one result
was partial or degraded; state advances only for datasets that still met their
acknowledgement threshold. A backup status alone is not restore proof; a
subsequent successful `drill` is.

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

## Render-Only Podman Target Scaffold

`scripts/render_portfolio_sidecar_quadlets.py` is the first inactive deployment
slice for L3 targets. It does not turn a Podman network into a multi-host mesh:
WireGuard remains host-owned infrastructure, provisioned through the shared
`short-circuit` utility. Each production SFTP target is intended for a native
rootless Linux Podman host that owns the exact RFC 1918 WireGuard address in the
authoritative `targets.local.json`. A macOS Podman Machine is useful for
disposable image and Quadlet verification, but its Linux VM cannot be assumed
to bind the Mac's WireGuard interface. See the focused
[Podman-on-WireGuard target guide](podman-on-wireguard-sidecar.md) for the
complete schema, rendering, transfer, and future activation boundaries.

Create the additional ignored local deployment document from the repository
root:

```bash
python3 scripts/render_portfolio_sidecar_quadlets.py init-config \
  --deployment "$(pwd)/config/portfolio-sidecar/podman-mesh.local.json"
```

The result is owner-only generation zero with no coordinator, target set, or
targets. It is deliberately inert and does not alter `policy.local.json`,
`targets.local.json`, or sidecar state. To prepare a review, advance a copy to
generation one, keep exactly one standalone coordinator declaration, and add
at least three sorted mesh targets. The deployment must exactly match one
`mesh-only` set in the authoritative targets document: target-set and target
generations, target IDs, failure domains, private addresses, explicit high
SFTP ports, membership, and strict-majority threshold are bound into the
rendered manifest, together with a digest of the complete target document.
This renderer validates only that topology projection; it does not open the
repository, password, or identity files or validate unrelated target sets.
The full `portfolio_sidecar.py validate` governance path remains mandatory
before activation. Image values must be registry digests or full local
`sha256:` image IDs; tags are refused and every target unit uses `Pull=never`.

Render exactly one physical node per invocation:

```bash
python3 scripts/render_portfolio_sidecar_quadlets.py render \
  --deployment "$(pwd)/config/portfolio-sidecar/podman-mesh.local.json" \
  --targets "$(pwd)/config/portfolio-sidecar/targets.local.json" \
  --target-id TARGET_REVIEWED_MESH_001 \
  --output "$(pwd)/config/portfolio-sidecar/target-001.local.d"
```

That no-overwrite output contains one `.container`, its one `.volume`, and an
owner-only manifest. It never emits another node's unit. The target unit binds
only the reviewed private host address and high port, references named Podman
secrets without their values, keeps the image read-only except for explicit
tmpfs and one `nodev,nosuid,noexec` repository volume, drops all capabilities
before restoring the five capabilities exercised by the rootless SFTP smoke
test, and never mounts the Podman socket. The owned image contract lives under
`containers/portfolio-sidecar-sftp/`; its OpenSSH service permits only pinned
Ed25519 public-key authentication and forced `internal-sftp` access to the
Restic repository.

All generated units omit `[Install]`, and their manifests say
`activation_ready: false`. Rendering never invokes Podman, systemd, WireGuard,
or a secret provider and does not create a container, volume, secret, live
unit, firewall rule, route, or activation symlink. A separate future activation
gate must run on each native Linux target and prove the image is present by its
exact content address, secrets exist, the bind address belongs to the intended
WireGuard interface, firewall scope is private, storage is an independent
failure domain, every host key is transactionally bound to the coordinator's
custom-port `known_hosts`, the repository has a monitored quota or dedicated
bounded filesystem, and the reviewed resource limits are suitable.

On macOS, the disposable generator proof is:

```bash
bash tests/test_portfolio_sidecar_quadlet_generator_podman.sh
```

It stages one target bundle briefly outside every live Quadlet search path in
the Podman VM, selects that isolated directory through `QUADLET_UNIT_DIRS`, runs
the real generator in dry-run mode, verifies both generated user services with
`systemd-analyze`, proves that no container, volume, secret, or live service
was created, and cleans up.

The coordinator remains intentionally non-containerized. The optional
`render-coordinator-review` command emits a non-Quadlet
`.coordinator-review` artifact only; policy-derived mounts, state/spool writes,
scheduling, credential scope, and a fenced static host identity must be
designed before a coordinator unit can be activatable. Neither target rendering
nor network proximity authorizes coordinator promotion.

Podman secret replacement affects only newly created containers. Rotate client
keys with an overlap, prove the new key, recreate the target container, and
then revoke the old key. Rotate host keys with overlapping `[host]:port`
`known_hosts` entries. An image digest provides immutability, not publisher
provenance; image review/signing remains a separate gate.

## Snapshot And Durability Contract

Each committed dataset sequence records the portable manifest hash and a
separate restic snapshot ID for each acknowledged target. The manifest is part
of the Restic snapshot and is therefore client-encrypted with the payload.
Restic exit status zero plus one syntactically valid snapshot ID is
acknowledgement evidence only until a drill checks and restores it. For an
L3 set of three nodes, two acknowledgements form a strict majority. The latest
sequence may be recorded as degraded at two of three. Fewer than a strict
majority is a failed backup. Missing members are never silently removed, and a
`mesh-only` job never redirects to the hosted target.

The executable stores only the latest committed dataset state. It has no
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

The executable treats snapshots as append-only and immutable. It has no
`forget`, `prune`, or repository-deletion path. Capacity monitoring must
therefore assume unbounded growth until a separately reviewed retention
design exists. This is coordinator-enforced logical immutability, not SFTP
WORM storage: a compromised server account or administrator may still destroy
repository bytes. Server-side immutable snapshots may strengthen the target,
but do not relax the independent restore-drill requirement.

## Coordinator And Failover

The executable has exactly one statically selected coordinator. It owns the
local operation spool and should be the only host provisioned with target write
credentials. A same-host process lock serializes commands, but it is not a
distributed lease or fencing token. Moving authority to
another host is a manual fenced handoff: stop the old coordinator, revoke or
disable all of its target identities, prove that it can no longer write, and
only then provision the replacement. If exclusive ownership cannot be proven,
do not start the replacement. Coordinator loss pauses backups; it does not
promote a nearby mesh node automatically.

Automatic failover is planned behind a private replicated-state authority
using rqlite/Raft. That authority must commit membership, coordinator leases,
monotonic fence epochs, dataset sequences, per-target acknowledgements, repair
state, and restore evidence through quorum before a new coordinator may act.
Network proximity may select the closest healthy request endpoint, but it
must never select the authoritative writer or bypass quorum.

## Restore Drills

`drill` turns the committed acknowledgement into offline restore proof
for the static-filesystem adapter. For every replica recorded in state, it runs
`restic check --read-data`, inspects the exact snapshot ID recorded by the
committed state, requires that snapshot to carry exactly the expected
manifest-format/dataset/repository identity tags, and restores that recorded
snapshot into a new owner-only spool directory. It never restores over the
source. A newer snapshot that failed to reach the acknowledgement threshold is
an uncommitted orphan: it does not supersede or invalidate the last committed
restore proof. It remains uncommitted repository data until a separately
reviewed retention process handles it.

The verifier requires the restored manifest hash and dataset/repository
identity to match state, then streams every payload file through SHA-256. It
requires the exact manifest file set and byte counts and rejects missing or
extra nodes, links, hard links, special files, unsafe or traversing paths, and
non-private restored nodes. Payload files are never accumulated in memory.
Governance, policy, target credentials, state, and `known_hosts` are
revalidated around the network operation. SSH uses pinned hosts plus explicit
identity selection, one connection attempt, and a bounded connection timeout.

`--evidence` must name a new ignored file in the state control directory. The
command never overwrites evidence. Its owner-only JSON binds the registry ID,
registry/policy/target/state generations, policy/target hashes, a canonical
state hash, manifest format, and per-replica target/snapshot/status outcomes;
it contains no repository URI or local path. Normal completion removes the
temporary plaintext restore.

Exit zero means every configured replica restored and verified. Exit 3 means
the drill was degraded: an L3 strict majority may still be verified, but a
missing member remains visible; below quorum is `not-verified`. Every recorded
replica is attempted, and an L3 failure never causes hosted fallback. The
drill verifies portable files and does not replace an application's own
database consistency or recovery checks.

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
