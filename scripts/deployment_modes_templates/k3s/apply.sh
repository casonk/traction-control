#!/usr/bin/env bash
# TODO: apply.sh for @@APP@@. Contract: create the namespace, then the network policy, then the workload, in that order. Create a CronJob suspended. Render machine-specific values (paths, node, timezone) at apply time and never commit them. Pass secrets through a private temp file and a Secret, never a command line.
echo "deploy/k3s/apply.sh is a scaffold stub: implement it (see traction-control docs/deployment-modes.md)" >&2
exit 1
