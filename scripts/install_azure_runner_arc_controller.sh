#!/usr/bin/env bash
set -euo pipefail

RESOURCE_GROUP="rg-arc-pilot"
VM_NAME="arcpilot-vm"
AZURE_CLI_IMAGE="mcr.microsoft.com/azure-cli:2.91.0"
HELM_VERSION="v3.17.3"
HELM_SHA256="ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd"
ARC_VERSION="0.15.1"
AZURE_CONFIG_DIR="${AZURE_CONFIG_DIR:-${HOME}/.azure}"

usage() {
  cat <<'EOF'
Usage: install_azure_runner_arc_controller.sh

Enable k3s secret encryption and install the pinned ARC scale-set controller
on rg-arc-pilot/arcpilot-vm. This does not create the GitHub token secret or
install a repository runner scale set.
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

for command_name in podman stat find grep; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "required command not found: ${command_name}"
done

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
This will enable k3s secret encryption and install the ARC ${ARC_VERSION}
controller on Azure VM ${RESOURCE_GROUP}/${VM_NAME}. The operation restarts
k3s if encryption is not enabled. The VM remains billable.
EOF
printf 'Type CONTINUE to proceed: '
read -r confirmation
[[ "$confirmation" == "CONTINUE" ]] || fail "operation cancelled"

export ARC_CONTROLLER_SETUP
ARC_CONTROLLER_SETUP="$(cat <<REMOTE_SCRIPT
set -eu
temporary_dir="\$(mktemp -d)"
report_status() {
  result=\$?
  trap - EXIT
  rm -rf "\$temporary_dir"
  printf '\\n__ARC_CONTROLLER_SETUP_EXIT_STATUS=%s\\n' "\$result"
  exit 0
}
trap report_status EXIT

config_dir=/etc/rancher/k3s/config.yaml.d
config_file="\$config_dir/90-secrets-encryption.yaml"
mkdir -p "\$config_dir"
if [ -e "\$config_file" ] \
  && ! grep -Eq '^[[:space:]]*secrets-encryption:[[:space:]]*true([[:space:]]|$)' "\$config_file"; then
  printf 'error: refusing to overwrite unexpected K3s config %s\n' "\$config_file" >&2
  exit 1
fi
if grep -R -Eq '^[[:space:]]*secrets-encryption:[[:space:]]*false([[:space:]]|$)' \
  /etc/rancher/k3s/config.yaml "\$config_dir" 2>/dev/null; then
  printf 'error: an existing K3s config explicitly disables secrets encryption\n' >&2
  exit 1
fi
if ! grep -R -Eq '^[[:space:]]*secrets-encryption:[[:space:]]*true([[:space:]]|$)' \
  /etc/rancher/k3s/config.yaml "\$config_dir" 2>/dev/null; then
  printf 'secrets-encryption: true\n' > "\$config_file"
  chmod 0600 "\$config_file"
  systemctl restart k3s
fi

deadline=\$(( \$(date +%s) + 300 ))
until /usr/local/bin/kubectl wait --for=condition=Ready --timeout=10s nodes --all >/dev/null 2>&1; do
  if [ "\$(date +%s)" -ge "\$deadline" ]; then
    printf 'error: k3s node did not become Ready after enabling encryption\n' >&2
    journalctl -u k3s -n 60 --no-pager || true
    exit 1
  fi
  sleep 5
done

deadline=\$(( \$(date +%s) + 300 ))
while :; do
  if encryption_status="\$(/usr/local/bin/k3s secrets-encrypt status 2>&1)" \
    && printf '%s\n' "\$encryption_status" | grep -q '^Current Rotation Stage:' \
    && printf '%s\n' "\$encryption_status" | grep -q '^Server Encryption Hashes:'; then
    break
  fi
  if [ "\$(date +%s)" -ge "\$deadline" ]; then
    printf 'error: k3s encryption status did not become available\n' >&2
    printf '%s\n' "\$encryption_status" >&2
    exit 1
  fi
  sleep 5
done

printf '%s\n' "\$encryption_status"
printf '%s\n' "\$encryption_status" \
  | grep -q 'Server Encryption Hashes: All hashes match' || {
  printf 'error: k3s encryption configuration hashes do not match\n' >&2
  exit 1
}

if printf '%s\n' "\$encryption_status" \
  | grep -q 'Current Rotation Stage: start'; then
  /usr/local/bin/k3s secrets-encrypt rotate-keys
elif printf '%s\n' "\$encryption_status" \
  | grep -q 'Current Rotation Stage: rotate'; then
  /usr/local/bin/k3s secrets-encrypt reencrypt
elif ! printf '%s\n' "\$encryption_status" \
  | grep -q 'Current Rotation Stage: reencrypt_finished'; then
  printf 'error: unexpected k3s encryption rotation stage\n' >&2
  exit 1
fi

deadline=\$(( \$(date +%s) + 300 ))
while :; do
  if encryption_status="\$(/usr/local/bin/k3s secrets-encrypt status 2>&1)" \
    && printf '%s\n' "\$encryption_status" \
      | grep -q 'Encryption Status: Enabled' \
    && printf '%s\n' "\$encryption_status" \
      | grep -q 'Current Rotation Stage: reencrypt_finished' \
    && printf '%s\n' "\$encryption_status" \
      | grep -q 'Server Encryption Hashes: All hashes match'; then
    break
  fi
  if [ "\$(date +%s)" -ge "\$deadline" ]; then
    printf 'error: Kubernetes data encryption did not finish successfully\n' >&2
    printf '%s\n' "\$encryption_status" >&2
    exit 1
  fi
  sleep 5
done
printf '%s\n' "\$encryption_status"

if ! command -v helm >/dev/null 2>&1 || ! helm version --short | grep -q '${HELM_VERSION}'; then
  curl -fsSL --retry 3 --connect-timeout 15 \
    https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz \
    -o "\$temporary_dir/helm.tar.gz"
  printf '%s  %s\n' '${HELM_SHA256}' "\$temporary_dir/helm.tar.gz" \
    | sha256sum --check
  tar -xzf "\$temporary_dir/helm.tar.gz" -C "\$temporary_dir"
  install -m 0755 "\$temporary_dir/linux-amd64/helm" /usr/local/bin/helm
fi

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm upgrade --install arc \
  --namespace arc-systems \
  --create-namespace \
  --version ${ARC_VERSION} \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller

/usr/local/bin/kubectl wait \
  --for=condition=Available \
  --timeout=300s \
  deployment \
  --all \
  --namespace arc-systems
/usr/local/bin/kubectl get deployments,pods --namespace arc-systems
REMOTE_SCRIPT
)"

podman run --rm \
  --env ARC_CONTROLLER_SETUP \
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
      --scripts "$ARC_CONTROLLER_SETUP" \
      --only-show-errors \
      --query "value[].message" \
      --output tsv)"
    printf "%s\n" "$result" | grep -Fvx "__ARC_CONTROLLER_SETUP_EXIT_STATUS=0" || true
    printf "%s\n" "$result" | grep -Fxq "__ARC_CONTROLLER_SETUP_EXIT_STATUS=0" || {
      printf "error: ARC setup failed inside the VM; inspect output above\n" >&2
      exit 1
    }
  '
