# Podman-on-WireGuard sidecar targets

This is a render-only deployment boundary for the mesh-only SFTP replicas used
by the ignored-state sidecar. Podman remains local to each Linux host;
WireGuard supplies the private routed address between hosts. The renderer does
not create a multi-host Podman network, attest WireGuard membership, configure
a firewall, or expose the Podman API or socket.

`scripts/render_portfolio_sidecar_quadlets.py` never invokes `podman`,
`systemctl`, WireGuard, or a secret provider. It validates ignored owner-only
input and creates a new ignored owner-only output directory. It refuses to
overwrite an existing output. A missing `manifest.json` means a failed render
left an incomplete directory that must be inspected rather than installed.

## Platform boundary

The rendered target Quadlets are for native rootless Podman on Linux. The Linux
host must own the exact RFC 1918 address on its WireGuard interface. A Podman
bridge remains host-local, and `PublishPort` binds one explicit high host port
on that address.

A macOS Podman machine does not directly own the macOS host's WireGuard
interface. Use it only for disposable syntax and container verification unless
the separate host-to-VM route and packet-filter behavior have been proven.

The WireGuard lifecycle remains owned by the portfolio's WireGuard utility.
This renderer neither creates peers nor treats an RFC 1918 literal as proof
that traffic used the mesh.

## Private configuration

Create an inert generation-zero document inside the existing private sidecar
control directory:

```bash
SIDECAR_CONTROL_ROOT="$(pwd)/config/portfolio-sidecar"
python3 scripts/render_portfolio_sidecar_quadlets.py init-config \
  --deployment "${SIDECAR_CONTROL_ROOT}/podman-mesh.local.json"
```

The parent directory must be mode `0700`. The local document is mode `0600`,
must be ignored by a tracked and unchanged worktree `.gitignore`, and is never
overwritten. `config/portfolio-sidecar/podman-mesh.example.json` is a wholly
synthetic schema example, not an operational configuration to activate.

An active generation binds one content-addressed coordinator image and at least
three mesh target descriptions to one `target_set_id`. Images use either a
registry `name@sha256:<full-digest>` reference or a full local
`sha256:<image-id>` with `Pull=never`; mutable tags are rejected. Validation
also requires the ignored `targets.local.json` and proves an exact match for
the selected topology projection: target IDs, distinct failure domains, RFC
1918 addresses, and explicit high SFTP ports. The resulting manifest binds the
complete target document digest, registry identity and generation, target
generation, strict-majority acknowledgement threshold, and a canonical
topology digest without copying credential paths.

This renderer does not open or validate the target document's repository,
password, or identity files and does not validate unrelated target sets. Its
manifest therefore records `full_sidecar_target_validation_performed: false`.
Before activation, run the existing private-sidecar validation path against
the complete private/public registry pair, policy, targets, catalog, and
credentials; the topology check here is not a substitute for that governance
gate.

Validate an active document with:

```bash
python3 scripts/render_portfolio_sidecar_quadlets.py validate \
  --deployment "${SIDECAR_CONTROL_ROOT}/podman-mesh.local.json" \
  --targets "${SIDECAR_CONTROL_ROOT}/targets.local.json"
```

## Per-node rendering

Render exactly one physical target per invocation and transfer only that
owner-only output directory to the corresponding Linux node:

```bash
python3 scripts/render_portfolio_sidecar_quadlets.py render \
  --deployment "${SIDECAR_CONTROL_ROOT}/podman-mesh.local.json" \
  --targets "${SIDECAR_CONTROL_ROOT}/targets.local.json" \
  --target-id TARGET_LOCAL_MESH_001 \
  --output "${SIDECAR_CONTROL_ROOT}/target-001.local.d"
```

One target bundle contains only that node's `.container`, its persistent
`.volume`, and a manifest. It never contains another node's unit or the
coordinator artifact. This separation matters because Quadlet searches unit
directories recursively.

The target contract is:

- registry image pinned by `@sha256` or a full local `sha256` image ID, with
  `Pull=never`;
- rootless bridge networking and an exact
  `mesh_address:published_port:container_port/tcp` binding;
- both ports at or above `1024`;
- read-only root filesystem, explicit `/run` and `/tmp` tmpfs mounts,
  `no-new-privileges`, and a narrow declared sshd capability set;
- one persistent repository volume mounted
  `rw,nodev,nosuid,noexec,Z` at `/srv/portfolio-sidecar/repository`;
- host key and authorized-key Podman secrets mounted at
  `/run/secrets/sidecar-host-key` and
  `/run/secrets/sidecar-authorized-keys`; and
- `SIDECAR_SFTP_PORT`, `SIDECAR_SFTP_REPOSITORY`,
  `SIDECAR_SFTP_HOST_KEY`, and `SIDECAR_SFTP_AUTHORIZED_KEYS` passed to the
  image entrypoint.

The owned target image and its OpenSSH policy live in
`containers/portfolio-sidecar-sftp/`. Build artifacts remain local until a
separate image-distribution review establishes provenance; the deployment may
refer to a reviewed local image by its full `sha256:` ID. The disposable smoke
proof is:

```bash
bash tests/test_portfolio_sidecar_sftp_image_podman.sh
```

It creates only synthetic keys and temporary Podman resources, then proves the
custom port, pinned host key, key-only forced-SFTP behavior, exact capability
set, read-only root, safe chroot ownership, and repository-volume persistence
across container recreation before cleaning up.

On macOS, also pass one rendered target through the real rootless generator:

```bash
bash tests/test_portfolio_sidecar_quadlet_generator_podman.sh
```

That proof uses Quadlet dry-run mode inside the Podman VM. It confirms the
container/volume dependency and exact generated Podman arguments, verifies the
two generated user services with `systemd-analyze`, creates no container,
volume, secret, or live systemd unit, and removes its temporary rootless
isolated input directory.

Secret names are rendered, never secret values or coordinator credential
paths. Replacing a Podman secret does not update a container that already
exists; a future activation workflow must stop and recreate the target during
rotation.

The deployment document currently names the host-key secret but does not bind
its public fingerprint to the coordinator's pinned `known_hosts`. The manifest
records that gap explicitly. Activation and host-key rotation must remain
blocked until one reviewed transaction binds the target secret, target
identity, custom-port `[host]:port` entry, and coordinator `known_hosts`
evidence.

The Quadlet intentionally has no `[Install]` section. Its manifest keeps
`activation_ready` and `activation_performed` false. Do not copy it into a
Quadlet search path or start it until all manifest prerequisites have passed:
the image contract and minimal sshd capabilities need a native rootless Linux
test, the Podman secrets and volume need provisioning, the host address and
firewall scope need verification, and host-specific resource limits need
review. The repository volume also needs a monitored quota or a dedicated
bounded filesystem; a plain local named volume does not prevent a target from
filling the node's root filesystem.

## Coordinator boundary

Render the current coordinator design boundary separately:

```bash
python3 scripts/render_portfolio_sidecar_quadlets.py render-coordinator-review \
  --deployment "${SIDECAR_CONTROL_ROOT}/podman-mesh.local.json" \
  --targets "${SIDECAR_CONTROL_ROOT}/targets.local.json" \
  --output "${SIDECAR_CONTROL_ROOT}/coordinator-review.local.d"
```

The coordinator output uses the deliberately unrecognized
`.coordinator-review` suffix. It is not a Quadlet. The executable coordinator
remains the existing locked, host-controlled, single writer until exact
policy-derived read-only repository mounts, writable control paths, scheduling,
restore evidence, and secret-rotation behavior are designed and tested.
Automatic promotion or nearest-node writer selection remains unsupported until
the quorum authority supplies leases and fencing tokens.
