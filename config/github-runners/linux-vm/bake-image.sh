# Bakes the golden Linux runner image on the template VM (Ubuntu 24.04): the CI tools, a `runner` user and the Actions
# runner. Run as root through Azure Run Command by scripts/bake_azure_linux_image.sh, which prepends:
#   RUNNER_VERSION='x.y.z'      the pinned runner release
# The per-job VM's cloud-init script (services/win-broker, LinuxUserData) then does:
#   cd /opt/actions-runner && runuser -u runner -- ./run.sh --jitconfig '<single-use config>'
set -eu
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates curl git jq unzip tar xz-utils gnupg acl \
  build-essential python3 python3-venv python3-pip \
  shellcheck age ruby docker.io >/dev/null

# The job user: passwordless sudo and Docker, because the point of this class is what a runner pod cannot do.
id runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --uid 1001 runner
usermod -aG docker runner
printf 'runner ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/90-runner
chmod 0440 /etc/sudoers.d/90-runner

# The Actions runner, verified against the digest GitHub publishes for the release asset.
name="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
rel="$(curl -fsSL -H 'User-Agent: runner-image-bake' "https://api.github.com/repos/actions/runner/releases/tags/v${RUNNER_VERSION}")"
url="$(printf '%s' "$rel" | jq -r --arg n "$name" '.assets[] | select(.name == $n) | .browser_download_url')"
digest="$(printf '%s' "$rel" | jq -r --arg n "$name" '.assets[] | select(.name == $n) | .digest')"
if [ -z "$url" ] || [ "$digest" = "null" ] || [ -z "$digest" ]; then
  echo "error: runner ${RUNNER_VERSION} has no Linux x64 package with a published digest; refusing an unverified file" >&2
  exit 1
fi
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/runner.tgz" "$url"
actual="sha256:$(sha256sum "$tmp/runner.tgz" | cut -d' ' -f1)"
if [ "$actual" != "$digest" ]; then
  echo "error: digest mismatch for $name (got $actual, expected $digest)" >&2
  exit 1
fi
echo "verified $name $actual"
rm -rf /opt/actions-runner
mkdir -p /opt/actions-runner
tar -xzf "$tmp/runner.tgz" -C /opt/actions-runner
rm -rf "$tmp"
/opt/actions-runner/bin/installdependencies.sh >/dev/null
chown -R runner:runner /opt/actions-runner

# Show what jobs will find, and fail the bake rather than ship an image that cannot run them.
echo "git=$(git --version)"
echo "docker=$(docker --version)"
echo "jq=$(jq --version)"
echo "runner=$(runuser -u runner -- /opt/actions-runner/bin/Runner.Listener --version)"
runuser -u runner -- sudo -n true
echo "sudo=ok"

# Leave nothing of this bake behind.
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
rm -rf /opt/actions-runner/_diag /opt/actions-runner/_work
echo "baked runner=${RUNNER_VERSION}"
