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

That runner stages only the three sidecar/registry modules and their synthetic
tests. It disables networking, uses a read-only root filesystem, drops all
Linux capabilities, enables `no-new-privileges`, runs as an unprivileged user,
and mounts no host checkout, ignored data, credentials, SSH agent, or container
socket. Fake SFTP/Restic endpoints exercise hosted-encrypted and mesh-majority
behavior without transmitting any live content.
