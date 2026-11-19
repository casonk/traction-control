# Deployment modes: one setting, K3s optional

Some portfolio services can run in three ways. This document is the contract every such service follows, so a
developer who has never heard of K3s can run any of them, and an operator can move one into the cluster and
back with a single command. It also records the platform conventions the cluster side depends on.

Reference implementations (read these before writing a new one; two live in private repositories, so they are
described here rather than named):

| Service | Kind | Repository | What its `k3s` mode adds |
|---|---|---|---|
| a private GPS-trace web app | long-running web service, takes inbound traffic | a private repository | a pod behind a loopback forwarder; Caddy and phones are unchanged |
| crew-chief listener | worker that polls a mailbox | `crew-chief` | a pod with default-deny egress; the host listener stops first |
| shock-relay digest sender | hourly job with credentials | `shock-relay` | a CronJob; Gmail credentials resolved from the vault on the host into a Secret, the pod config names variables only |
| intake | daemon whose risk is parsing untrusted files | `intake` | a sandbox for the parsers only: no network, no secrets, none of your files; the daemon stays on the host (see below) |
| a private Spotify-history poller | periodic job | a private repository | a CronJob, created suspended, handed work only after `verify.sh` passes |

## The contract

Every service in scope has `deploy/deploy.sh` and these properties. A service that does not match is not
following the pattern.

| Rule | Detail |
|---|---|
| Entry point | `deploy/deploy.sh [--mode dev\|host\|k3s] up\|down\|status\|verify\|mode` |
| Modes | `dev` (foreground or one run, language runtime only), `host` (a systemd user unit or timer), `k3s` (a pod or CronJob) |
| Default | `dev`. A fresh checkout must run with no systemd, no Clockwork and no cluster |
| Selection | first match wins: `--mode`, then `<APP>_DEPLOY_MODE` in the environment, then `<APP>_DEPLOY_MODE` in `config/deploy.local.env`, then `dev` |
| Config files | tracked `config/deploy.example.env` (every mode named, `dev` selected); untracked `config/deploy.local.env`. **Check it is gitignored in the repository** (`git check-ignore`); one repository was missing the rule |
| No silent fallback | an unknown mode, or a config file that cannot be read, exits 2 and names the source. Never `\|\| true` around reading the config |
| Prerequisites | `k3s up` checks for kubectl, podman and the registry **before changing anything** and exits 1 saying what to install and that `dev` and `host` need neither |
| Exit codes | `0` success, `1` a prerequisite is missing or a step failed, `2` usage or an invalid mode |
| App code is mode-agnostic | nothing under `src/` reads the mode. The cluster differs by *configuration* (environment variables with standalone defaults), never by code path |
| Switching | one command each way, and the data never moves (it stays on the host in every mode) |
| Rollback | `--mode k3s down` is the same as `--mode host up`; the pod is left in place, stopped, so rolling forward again is fast |

### Handing work over safely

Two copies of the same work are the main hazard, and the three kinds of service need different handling:

- **Service with a port** (the GPS-trace web app): the pod is reached through a socket-activated forwarder on the loopback
  address the host service used, so the front end (Caddy) never changes. `on` stops **and disables** the host
  unit (a unit that is only stopped races the forwarder for the port at the next login); `off` does the reverse.
- **Worker** (the listener): two workers on one mailbox answer every message twice. `up` stops the host worker
  first, runs the steps, and on any failure scales the pod to 0 and restarts the host worker if it had been
  running. `host up` scales the pod to 0 *before* starting the host worker, and refuses if it cannot (no
  kubectl) rather than start a second one.
- **Periodic job** (the Spotify-history poller): the CronJob is created `suspend: true`; the host timer keeps running until
  `verify.sh` passes, then `up` disables the timer and unsuspends the job. A failed verify changes nothing.
- **Triggered job** (a heavy weekly download-and-plot refresh): a job whose unit carries host-side guards (traction-control's
  job guard, the resource arbiter's CPU and I/O limits, a time limit) or a post-step that needs host credentials (opening a pull
  request with `gh`) must keep its timer: a CronJob would bypass all of them. In `k3s` mode the timer and unit stay
  exactly as they are and `up` installs a removable systemd drop-in that replaces only `ExecStart`, with
  `deploy/k3s/run-job.sh`, installed as `zz-k3s-run.conf` because systemd applies drop-ins in name order and the last `ExecStart=` wins (a unit with its own interpreter override would otherwise silently beat it). That script creates one Job from a CronJob that stays `suspend: true` for ever (its
  schedule is a date that never occurs; it is only a template), streams the pod's log into the journal, deletes the
  Job if systemd stops the unit, and exits with the job's status. `up` never disables the timer; `host up` removes the
  drop-in. The checker asserts the timer is never disabled and that the drop-in sets nothing but `Environment` and
  `ExecStart`.

When systemd cannot tell which mode last held the work (a CronJob is invisible to it), `up` writes a marker file
under `~/.local/state/<app>/deploy-mode`, and `host up` reads it to know the pod must be stopped first.

## Tests that every implementation carries

Run the script against **stub commands that record every call**, so no test needs systemd, podman or a cluster:

1. the default is `dev`; precedence is flag, environment, config file, default; an unknown or unreadable
   selection is an error
2. `dev` and `host` (`mode`, `status`, `verify`, `down`) never call kubectl or podman
3. `k3s up` refuses without kubectl, and without the registry, and runs none of the steps
4. the steps run **in order**, and the hand-over (stopping the host copy, unsuspending the job) happens only
   after `verify.sh`
5. a failing step leaves the previous copy running (or restores it), and writes no marker
6. leaving `k3s` stops the pod before the host copy starts; and refuses, rather than double-run, when it cannot
7. the image build refuses uncommitted source unless told otherwise
8. host-run scripts use no Python flag newer than the oldest supported Python (see below)
9. the example config, the docs and the README link all exist and name every mode
10. every script under `deploy/` is executable (a script written by a tool that does not set the mode made `k3s up`
    stop at the verify step with "Permission denied")
11. a failure **inside a function** still runs the restore trap (see below), and a failed bridge, verify or daemon
    restart leaves the previous behaviour in place

Prove each safety test fails against the broken behaviour (delete the restore, reorder the stop) before trusting
it. Mutation checks like these caught real mistakes.

## Variant: sandbox one function, keep the service where it is

Sometimes the risk is one step of a large service, not the service. The intake daemon holds a mailbox, API keys
and other repositories, so it cannot move into a pod without widening the pod's reach, but its PDF, image and
video parsers read files an attacker can shape. The pattern still applies; what the modes mean changes:

- `host`: the parsers run in the service's own process (the default; nothing else is needed).
- `dev`: the sandbox runs in the foreground on the machine, so a developer can try the sandbox path without a cluster.
- `k3s`: the sandbox runs in a pod, and **the service is pointed at it by a systemd drop-in** that sets one
  environment variable. Writing the drop-in and removing it are the whole switch, so rollback is exact.

Rules this variant adds:

- The sandbox has **no network, no secrets, no environment, and none of your files**. It receives bytes and returns
  text, PNG bytes or a yes/no, so the service never opens an untrusted file with a parser and never decodes the
  pixels it gets back. Its only volume is a bounded scratch directory.
- It **fails closed**. If the sandbox is unreachable the service skips the work and leaves the files where they
  are; it never parses locally instead, because a fallback lets a crash steer a hostile file into the weaker
  place. An outage is "unavailable" and never marks a document corrupt.
- Each document runs in a **child process with a CPU limit and a timeout**, so a hostile file can hang or crash a
  child, not the service. Errors never echo document content.
- Point the service at the sandbox **only after the pod is verified and reachable through the bridge**, and make
  any failure from the bridge onward restore the previous state (bridge down, drop-in removed, service restarted
  only if it had been pointed there).

## K3s platform conventions

These hold for every service on the single node. Defaults are chosen so a mistake fails closed.

**Images.** Build with podman and push to the in-cluster registry; no `sudo k3s ctr images import`. The registry
is a ClusterIP only the node can reach (Cilium denies every pod; it has no egress). A pod names its image
`localhost:5000/<name>:<tag>`; a one-time containerd mirror maps that to the registry. Build **only from
committed source**: the host runs the checkout and the pod runs the image, and a dirty checkout made the two
write different `summary.json` formats.

**Namespace and pod.** One namespace per service labelled `pod-security.kubernetes.io/enforce: restricted`.
Non-root, read-only root filesystem, drop all capabilities, no service-account token, `Recreate` for anything
with one writer. Memory limits come from a measurement (`/usr/bin/time -v`), not a guess; a 256 MiB guess was
OOM-killed at 417 MB and left a file stale.

**Host data.** Data stays in its host folder, mounted through a static `local` PersistentVolume with reclaim
policy `Retain`, rendered at apply time so no path or node name is committed. Run the pod as the owner's uid
(the folders are mode 0700). **Never set `fsGroup`**: on a local volume Kubernetes would recursively re-group
the real files. SELinux keeps containers out of ordinary home files; label exactly the needed folders with
`chcon -R -t container_file_t` (no sudo) and document `restorecon -RF` to undo. A system-wide relabel resets
the label, so the apply script refuses to run when it is missing.

**Network.** Cilium default deny, both ways. A pod gets only what it names: DNS for the exact hostnames and
`toFQDNs` rules on port 443 (the search-domain lookups the resolver tries first are dropped, which is correct).
Deny rules for host, remote nodes, the API server and link-local addresses sit beside the allows, because deny
wins. A service with no outbound need gets `egressDeny` of everything. Inbound is `fromEntities: [host]` only.

**Settings that differ in a pod** are ordinary environment variables whose defaults are the standalone
behaviour, so the pod needs no special code path. Seen so far: a bind address, a list of proxies trusted to
vouch for forwarded identity (a pod never sees loopback peers), a switch to show what the host measured instead
of measuring (a pod cannot see the host's interfaces or VPN), allowed `Host` names for a rebinding guard (the pod
has no site store), and the timezone (a pod defaults to UTC and shifted every per-day count).

**Secrets and env files.** Read them the way systemd does, with quotes stripped (`KEY="a b"` is valid for
systemd and `kubectl --from-env-file` keeps the quotes literally). Pass them through a private temp file, never
the command line, and a Secret, never a manifest.

**Restore traps and functions.** `trap restore ERR` is not inherited by shell functions unless `set -E` is on, so
a failure inside a function (`bridge`, `install_dropin`) skipped the restore and would have left a service pointed
at a sandbox that never came up. Use `set -eEuo pipefail` in any `deploy.sh` that has a restore trap, and test a
failure inside each function.

**Host scripts and the Python floor.** Use `python3 -I`, not `python3 -P` (3.11 and newer), in anything that runs
on the host, and never hide a failure with `|| true`. Inside a pod image, `-P` is fine because the image pins the
version.

## `verify.sh` must be able to fail

- A check that cannot run is a `FAIL`, never an `ok`. No pod, no probe result, a pod that is not ready: exit 2
  with "nothing was verified".
- Test the property that matters, from the right place: egress from a pod that carries the service's labels;
  reachability from another namespace (it must time out); identity headers honoured only from the named proxy;
  a registered hostname admitted and a stranger's refused.
- **Include a burst.** A single request passed while the real page load (about 15 requests at once) killed the
  forwarder. Send a few dozen concurrently and check the socket survived.
- Confirm the pod does not write what it must not (compare a row count before and after).

## Addresses and ports in use

Check `ss -ltn` and this table before choosing one. Fixed ClusterIPs make a host-side forwarder or a mirror file
independent of pod restarts.

| What | Address | Notes |
|---|---|---|
| In-cluster image registry | ClusterIP `10.43.0.50:5000` | node-only; `localhost:5000/<name>` for pods |
| web-app pod (GPS trace) | ClusterIP `10.43.0.60:5434` | next free in the portfolio range: `10.43.0.63` and `10.43.0.65`, then up to `10.43.0.99` |
| web app on the host (GPS trace) | `127.0.0.1:5434` | the host service, or the forwarder in `k3s` mode |
| intake extractor sandbox pod | ClusterIP `10.43.0.61:5455` | node-only, no egress at all |
| intake extractor on the host | `127.0.0.1:5455` | the foreground service in `dev`, or the forwarder in `k3s` mode |
| torrent web UI pod | ClusterIP `10.43.0.62:5400` | reaches the host's Transmission RPC through one port-limited allow |
| torrent web UI on the host | `127.0.0.1:5400` | the host service, or the forwarder in `k3s` mode |
| File Browser pod (snowbridge) | ClusterIP `10.43.0.64:8080` | node-only, no egress at all; mounts each real source folder of the share |
| File Browser on the host (snowbridge) | `127.0.0.1:8080` | the rootful host container, or the forwarder in `k3s` mode; Caddy proxies here |
| Transmission RPC forwarder | pod-network address (`cilium_host`) `:9091` | user socket forwarding to the host's loopback RPC; only the pod's policy admits it |
| Hubble UI forward | `127.0.0.1:8195` | `forward.sh ui` |
| Prometheus forward | `127.0.0.1:9190` | `forward.sh prometheus` |
| Hubble relay forward | `127.0.0.1:4245` | `forward.sh relay` |

Do not use 8081 (the password UI), 8090 and 8091 (the remote-desktop dev container) or 9090 (the machine's web
console); one forward already failed on a taken port. Allocate new ClusterIPs in `10.43.0.50`-`10.43.0.99` and
add them here in the same change.

## Adding a new service: scaffold, fill in, check

Do not copy a `deploy.sh` from another repository by hand: the fixes learned the hard way (`set -E`, executable bits,
the dirty-source guard, restore traps) are in the templates, and the generated tests prove them.

```bash
python3 scripts/scaffold_deployment_modes.py --repo ../my-app --app my-app --kind job
python3 scripts/scaffold_deployment_modes.py --repo ../my-app --app my-app --kind service --cluster-ip 10.43.0.63 --port 5463
```

Choose the kind from how the thing behaves (`job` periodic, `triggered` periodic but guarded or post-processed on the host, `worker` must never run twice, `service` has a port,
`sandbox` isolates one risky step of a service). Useful options: `--unit`, `--namespace`, `--dev-up` (how it runs once
in the foreground), `--source` (paths baked into the image and guarded against uncommitted changes), `--host-data`
(the pod mounts host folders, so also generate `prepare-host.sh`), `--dry-run`, `--force`. It refuses an address or
port already in the table below, and writes nothing if anything is wrong.

What you get, all passing from the first run:

- `deploy/deploy.sh` for the kind, `config/deploy.example.env`, `docs/deployment.md`, a README section and a
  `.gitignore` rule for `config/deploy.local.env`
- `deploy/k3s/`: `env_value.py`, `build-image.sh` (with the dirty-source guard), and `handover.sh` (job) or
  `bridge.sh` and its systemd units (service, sandbox)
- `tests/test_deploy_modes.py`: the contract's tests above, for that kind, against stub commands

What you must write, deliberately left as stubs that **fail loudly** (`apply.sh`, `verify.sh`, `Containerfile`,
`manifests/`, and `prepare-host.sh` with `--host-data`): the parts that depend on what the app is. Each stub says what
the contract requires of it. Then:

1. fill in the stubs; the generated tests keep passing as you go
2. `python3 scripts/check_deployment_modes.py --repo ../my-app` (FAIL means the contract is broken, WARN means a stub
   remains); run it in review and wherever you audit the portfolio
3. add the service's ClusterIP and ports to the table above, in the same change that adds the service

The checker needs no cluster. It checks the files, the selection order, `set -E`, the config read, the hand-over order
for the kind, executable bits, the gitignore rule, the docs and tests, and the manifests (restricted namespace, no
`hostPath`, no `fsGroup` on a pod that mounts host files, a suspended CronJob, a network policy).
