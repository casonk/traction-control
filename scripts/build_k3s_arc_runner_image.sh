#!/usr/bin/env bash
# build_k3s_arc_runner_image.sh — build the runner image with the CI tools and push it to the in-cluster registry.
# No sudo. Needs crew-chief's deploy/k3s/push-image.sh (it owns the registry address). Pods use
# localhost:5000/arc-runner:RUNNER_VERSION; point scale-set-values.yaml.template at it and re-run the installer.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CTX="$HERE/../config/github-runners/k3s/runner-image"
RUNNER_VERSION="${RUNNER_VERSION:-2.338.2}"
PUSH="${CREW_CHIEF_DIR:-$HERE/../../crew-chief}/deploy/k3s/push-image.sh"
[ -x "$PUSH" ] || { echo "cannot find crew-chief's push-image.sh; set CREW_CHIEF_DIR" >&2; exit 1; }
podman build --build-arg "RUNNER_VERSION=$RUNNER_VERSION" -t "localhost/arc-runner:$RUNNER_VERSION" -f "$CTX/Containerfile" "$CTX"
"$PUSH" "localhost/arc-runner:$RUNNER_VERSION" "arc-runner:$RUNNER_VERSION"
