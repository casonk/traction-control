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

Prove each safety test fails against the broken behaviour (delete the restore, reorder the stop) before trusting
it. Mutation checks like these caught real mistakes.

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
| web-app pod (GPS trace) | ClusterIP `10.43.0.60:5434` | next free in the portfolio range: `10.43.0.61`, then up to `10.43.0.99` |
| web app on the host (GPS trace) | `127.0.0.1:5434` | the host service, or the forwarder in `k3s` mode |
| Hubble UI forward | `127.0.0.1:8195` | `forward.sh ui` |
| Prometheus forward | `127.0.0.1:9190` | `forward.sh prometheus` |
| Hubble relay forward | `127.0.0.1:4245` | `forward.sh relay` |

Do not use 8081 (the password UI), 8090 and 8091 (the remote-desktop dev container) or 9090 (the machine's web
console); one forward already failed on a taken port. Allocate new ClusterIPs in `10.43.0.50`-`10.43.0.99` and
add them here in the same change.

## Adding a new service

1. Write the app so every mode-dependent value is an environment variable defaulting to standalone behaviour.
2. Add `deploy/deploy.sh`, `config/deploy.example.env`, `docs/deployment.md`, and a README link; add the
   gitignore rule for `config/deploy.local.env` and check it.
3. Add `deploy/k3s/` (Containerfile, `build-image.sh`, `prepare-host.sh`, `apply.sh`, `verify.sh`, manifests).
4. Copy the tests above and run the mutation checks.
5. Add the service to the reference table here and its address and ports to the table above.
