#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="rg-arc-pilot"
VM_NAME="arcpilot-vm"
AZURE_CLI_IMAGE="mcr.microsoft.com/azure-cli:2.91.0"
PROBE_IMAGE="curlimages/curl:8.12.1"
AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-${HOME}/.azure}"
POLICY_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/github-runners/runner-network-policy.yaml"

usage() {
  cat <<'EOF'
Usage: configure_azure_runner_network_policy.sh

Apply and verify the dotfiles-pilot runner network policy on
rg-arc-pilot/arcpilot-vm. The verification creates and removes a temporary
probe pod; it does not create runner credentials or a runner scale set.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
[[ "$#" == "0" ]] || fail "this script does not accept arguments; use --help"

for command_name in podman stat find grep base64 cat; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command not found: ${command_name}"
done

[[ -r "$POLICY_FILE" ]] || fail "network policy file is missing: ${POLICY_FILE}"
[[ -d "$AZURE_CONFIG_DIR" && ! -L "$AZURE_CONFIG_DIR" ]] \
  || fail "Azure CLI config directory is missing or is a symlink: ${AZURE_CONFIG_DIR}"
[[ "$(stat -c '%u' "$AZURE_CONFIG_DIR")" == "$(id -u)" ]] \
  || fail "Azure CLI config directory must be owned by the current user"
[[ "$(stat -c '%a' "$AZURE_CONFIG_DIR")" == "700" ]] \
  || fail "Azure CLI config directory must have mode 0700"
if find "$AZURE_CONFIG_DIR" -type l -print -quit | grep -q .; then
  fail "Azure CLI config directory must not contain symlinks"
fi
unsafe_path="$(find "$AZURE_CONFIG_DIR" \( -type f -o -type d \) -perm /077 -print -quit)" \
  || fail "could not validate Azure CLI config permissions"
if [[ -n "$unsafe_path" ]]; then
  fail "Azure CLI files and directories must not grant group or other permissions"
fi

podman image exists "$AZURE_CLI_IMAGE" \
  || fail "Azure CLI image is not available locally: ${AZURE_CLI_IMAGE}"

cat <<EOF
This will apply the dotfiles-pilot runner network policy to
${RESOURCE_GROUP}/${VM_NAME} and run a temporary network probe. The probe
must reach public HTTPS but must not reach the Kubernetes API, VM node,
Azure metadata, or Azure platform address. No PAT or runner secret is used.
EOF
printf 'Type CONTINUE to proceed: '
read -r confirmation
[[ "$confirmation" == "CONTINUE" ]] || fail "operation cancelled"

export NETWORK_POLICY_B64
NETWORK_POLICY_B64="$(base64 -w0 "$POLICY_FILE")"
export NETWORK_POLICY_SETUP
NETWORK_POLICY_SETUP="$(cat <<REMOTE_SCRIPT
set -eu
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
probe_name=network-policy-probe
report_status() {
  result=\$?
  trap - EXIT
  /usr/local/bin/kubectl delete pod "\$probe_name" --namespace arc-runners \
    --ignore-not-found --wait=true >/dev/null 2>&1 || true
  printf '\\n__NETWORK_POLICY_SETUP_EXIT_STATUS=%s\\n' "\$result"
  exit 0
}
trap report_status EXIT

/usr/local/bin/kubectl create namespace arc-runners \
  --dry-run=client \
  --output=yaml \
  | /usr/local/bin/kubectl apply -f -
printf '%s' '${NETWORK_POLICY_B64}' | base64 -d \
  | /usr/local/bin/kubectl apply -f -
/usr/local/bin/kubectl delete pod "\$probe_name" --namespace arc-runners \
  --ignore-not-found --wait=true >/dev/null
/usr/local/bin/kubectl run "\$probe_name" \
  --namespace arc-runners \
  --image=${PROBE_IMAGE} \
  --labels=app.kubernetes.io/component=runner,actions.github.com/scale-set-name=dotfiles-pilot \
  --restart=Never \
  --command -- sh -c 'sleep 300'
/usr/local/bin/kubectl wait \
  --for=condition=Ready \
  --timeout=180s \
  "pod/\$probe_name" \
  --namespace arc-runners
/usr/local/bin/kubectl exec "\$probe_name" --namespace arc-runners -- sh -ec '
  curl --fail --silent --show-error --max-time 20 https://api.github.com/rate_limit >/dev/null
  echo public_https=allowed
  for target in https://10.43.0.1:443/ https://10.240.1.4:10250/pods http://169.254.169.254/metadata/instance?api-version=2021-02-01 http://168.63.129.16/; do
    if curl --noproxy "*" --silent --show-error --output /dev/null --connect-timeout 2 --max-time 4 --insecure -H "Metadata: true" "\$target"; then
      echo "unexpectedly reachable: \$target" >&2
      exit 1
    fi
    echo "blocked: \$target"
  done
'
/usr/local/bin/kubectl get networkpolicy --namespace arc-runners
REMOTE_SCRIPT
)"

podman run --rm \
  --env NETWORK_POLICY_SETUP \
  --volume "${AZURE_CONFIG_DIR}:/source:ro,z" \
  --entrypoint /bin/sh \
  "$AZURE_CLI_IMAGE" \
  -c '
    set -eu
    state_dir="$(mktemp -d /tmp/azure-state.XXXXXX)"
    trap '\''rm -rf "$state_dir"'\'' EXIT
    cp -a /source/. "$state_dir/"
    export AZURE_CONFIG_DIR="$state_dir"

    subscription_state="$(az account show --only-show-errors --query state --output tsv)"
    [ "$subscription_state" = "Enabled" ] || {
      printf "error: Azure subscription is not enabled\n" >&2
      exit 1
    }
    vm_state="$(az vm get-instance-view \
      --resource-group rg-arc-pilot \
      --name arcpilot-vm \
      --only-show-errors \
      --query "instanceView.statuses[?starts_with(code, '\''PowerState/'\'')].displayStatus | [0]" \
      --output tsv)"
    [ "$vm_state" = "VM running" ] || {
      printf "error: Azure VM is not running (state: %s)\n" "$vm_state" >&2
      exit 1
    }

    result="$(az vm run-command invoke \
      --resource-group rg-arc-pilot \
      --name arcpilot-vm \
      --command-id RunShellScript \
      --scripts "$NETWORK_POLICY_SETUP" \
      --only-show-errors \
      --query "value[].message" \
      --output tsv)"
    printf "%s\n" "$result" | grep -Fvx "__NETWORK_POLICY_SETUP_EXIT_STATUS=0" || true
    printf "%s\n" "$result" | grep -Fxq "__NETWORK_POLICY_SETUP_EXIT_STATUS=0" || {
      printf "error: network policy setup or probe failed; inspect output above\n" >&2
      exit 1
    }
  '
