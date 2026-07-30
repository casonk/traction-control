# Containerized installer tests

Run the light, moderate, and heavy Linux installer profiles in separate,
ephemeral containers:

```bash
bash scripts/install_podman_runtime.sh --dry-run
bash scripts/install_podman_runtime.sh
bash tests/test_install_traction_control_agents_containers.sh
```

Pass one or more tier names to narrow a run:

```bash
bash tests/test_install_traction_control_agents_containers.sh light heavy
```

The host runner prefers Podman, also accepts Docker, and builds
`tests/containers/Containerfile`. Set `CONTAINER_ENGINE=podman|docker` to make
the selection explicit. It uses the sibling `clockwork` and `archility` source
trees below the portfolio root; override those paths with
`TRACTION_CONTROL_CLOCKWORK_REPO` or `TRACTION_CONTROL_ARCHILITY_REPO` when
necessary.

On macOS, the runner targets `podman-machine-default` explicitly so it does not
depend on the ambient default connection. If the runtime bootstrap used a
custom `--machine-name`, pass the same name to the runner:

```bash
TRACTION_CONTROL_PODMAN_CONNECTION=my-machine \
  bash tests/test_install_traction_control_agents_containers.sh
```

On macOS, `scripts/install_podman_runtime.sh` uses Homebrew for an unattended
Podman CLI install and prepares a named rootless machine without replacing the
default Podman connection. That automated path is limited to Homebrew's current
Podman support floor of Apple Silicon and macOS 13 or newer. Use `--no-install`
after installing the upstream signed package on another supported Mac. The
default Podman machine configuration exposes the login user's home directory
to the VM; the test runner itself adds no host mounts. The optional
`--smoke-test` performs an external image pull, while the default runtime
verification does not.

Only the required source files are staged into a temporary build context. Each
container creates local Git remotes, disables networking, downloads its exact
support-repository bundle through real `git clone` calls, and renders units
with the real staged Clockwork code. It then checks the complete repository and
unit sets, validates every unit in user scope with `systemd-analyze`, reruns the
profile, and confirms that activation fails before writes because an ordinary
container has no systemd user manager. The verifier receives a private
mode-0700 `XDG_RUNTIME_DIR` because systemd 252 needs one for its in-process
user model; D-Bus remains unset, generators are disabled, and the harness
asserts that no user-manager bus is created.

The containers use a read-only root filesystem and a disposable `/tmp` tmpfs.
They never mount the host portfolio, credentials, service manager, container
socket, or scheduler directories. The test intentionally does not run any
agent workload or activate timers.

The ignored-state sidecar has a separate, narrow Linux regression image:

```bash
bash tests/test_portfolio_sidecar_containers.sh
```

That runner stages only the four sidecar/registry/rendering modules, their
three synthetic test modules, and the two synthetic topology examples. It
disables networking, uses a read-only root filesystem, drops all
Linux capabilities, enables `no-new-privileges`, runs as an unprivileged user,
and mounts no host checkout, ignored data, credentials, SSH agent, or container
socket. Fake SFTP/Restic endpoints exercise hosted-encrypted and mesh-majority
behavior without transmitting any live content.

Two focused Podman proofs cover the production-shaped target artifacts:

```bash
bash tests/test_portfolio_sidecar_sftp_image_podman.sh
bash tests/test_portfolio_sidecar_quadlet_generator_podman.sh
```

The first builds the owned key-only SFTP image and proves its authentication,
forced-SFTP, chroot, capability, read-only-root, and persistent-volume
contracts. The second renders one inactive target and passes its `.container`
and `.volume` files through the real rootless Quadlet generator inside the
macOS Podman VM. It uses dry-run mode, asserts that no Podman resource or live
systemd unit appears, verifies both generated services with `systemd-analyze`,
and removes the temporary isolated VM input directory.

The restore-proof path also has an opt-in real Podman regression:

```bash
bash tests/test_portfolio_sidecar_real_podman.sh
```

It builds dependencies before creating an internal-only Podman bridge, then
runs one coordinator against four real key-only OpenSSH/SFTP targets: one L2
hosted target and three fixed-address RFC 1918 L3 targets. The harness creates
fresh client and server identities, pinned `known_hosts`, Restic passwords,
repositories, governance documents, ignored source data, and drill evidence
under disposable synthetic test state. It exercises full L2/L3 backup and
restore drills, stops one mesh target, and proves that a two-of-three strict
majority commits and drills with degraded status. No host SSH material,
portfolio checkout, external runtime route, container socket, or live sidecar
configuration enters the test containers. Exact containers, volumes, network,
and temporary state are removed on exit.
