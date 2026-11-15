#!/usr/bin/env bash
# TODO: verify.sh for @@APP@@. Contract: prove isolation from inside a pod that carries the workload's labels. A check that cannot run must FAIL, never print ok; with no pod, exit 2 and say nothing was verified. Wait for any previous verification pod to be gone before creating the next. Include a burst of requests, and a check that another namespace cannot reach the workload.
echo "deploy/k3s/verify.sh is a scaffold stub: implement it (see traction-control docs/deployment-modes.md)" >&2
exit 1
