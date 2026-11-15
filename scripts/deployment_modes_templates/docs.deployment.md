# Deployment modes: @@APP@@

@@APP@@ runs three ways. You choose with one setting, and **only the `k3s` mode needs a cluster**. If you just
want to run it, you need Python and nothing else.

| Mode | What it is | Needs |
|---|---|---|
| `dev` (default) | @@DEV_BLURB@@ | the language runtime |
| `host` | @@HOST_BLURB@@ | systemd |
| `k3s` | @@K3S_BLURB@@ | K3s, podman, kubectl, the in-cluster registry |

The application code is the same in all three: nothing under `src/` knows which mode it is in.

## Choose a mode

```bash
deploy/deploy.sh mode                  # print the mode in effect and where it came from
deploy/deploy.sh up                    # run it in that mode
deploy/deploy.sh status | verify | down
```

The mode is read from, first match wins:

1. `--mode dev|host|k3s` on the command line
2. `@@PREFIX@@_DEPLOY_MODE` in the environment
3. `@@PREFIX@@_DEPLOY_MODE` in `config/deploy.local.env` (copy `config/deploy.example.env`; it is gitignored)
4. `dev`

An unknown value, or a config file that cannot be read, is an error, never a silent fallback.

## What each mode does

@@MODE_TEXT@@

## Switching

@@SWITCH_TEXT@@

`--mode k3s down` is the same as `--mode host up`. A marker in `~/.local/state/@@APP@@/` records which mode last
held the work, so `host up` knows what to stop first.

## For contributors without K3s

- `pytest` needs no cluster: the deployment tests parse manifests and run the scripts against stub commands.
- Nothing under `deploy/k3s/` runs unless you select the `k3s` mode.
- This file, the scripts and their tests started from traction-control's `scripts/scaffold_deployment_modes.py`;
  the contract is in its `docs/deployment-modes.md`. Run `scripts/check_deployment_modes.py --repo .` to check.
