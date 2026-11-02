# Self-hosted GitHub Actions runners

## Design

Use the maintained Actions Runner Controller (ARC) scale-set charts inside a
dedicated Linux VM. The controller and listener run in Kubernetes; runner pods
are created only for assigned jobs and deleted afterward. Keep the pilot at
zero idle runners and one concurrent job. The VM, not the runner pod, is the
boundary protecting the host from workflow code.

The GitHub account is a personal account, so runner scale sets are registered
per repository rather than shared through an organization. ARC supports both
fine-grained personal access tokens and GitHub Apps for repository runners.
Prefer a GitHub App for unattended operation: ARC mints short-lived
installation tokens from the App's private key. Give the App only repository
**Administration: read and write** and **Metadata: read** access, and install
it only where intended. The App private key remains a long-lived credential
that must be protected and rotated. A fine-grained PAT is also supported; scope
it to only the pilot repository with **Administration: read and write**. Do
not reuse a pilot PAT when enrolling other repositories. GitHub's ARC
authentication guide directs creating GitHub Apps under an organization. Since
this pilot repository is under a personal account, use PAT mode unless you
confirm that the App can be installed for this repository; App mode still
requires a successful runner smoke test before relying on it.

The pilot uses a dedicated `workflow_dispatch` workflow on the repository's
default branch. Existing CI and all pull-request/fork workflows stay on
GitHub-hosted runners. Do not route untrusted code to self-hosted runners.

## VM and cluster boundary

- For a local deployment, require working KVM acceleration on the host. Do not
  substitute software emulation or run jobs directly on the Fedora host.
- For the Azure pilot, use the short-lived single-VM scaffold in
  `infra/azure-runner/main.bicep`. It creates a North Central US Ubuntu VM,
  isolated VNet/subnet, static public IPv4 for outbound runner traffic, and an
  NSG that denies all inbound traffic. It does not install Kubernetes or ARC,
  create credentials, or deploy any Azure resources by itself.
- The Azure VM's outbound access is needed for GitHub and image/package
  downloads. The subnet NSG is not a pod-level egress boundary; enforce the
  runner network policies below before dispatching jobs.
- `config/github-runners/runner-network-policy.yaml` creates the dedicated
  `arc-runners` namespace and selects only the `dotfiles-pilot` runner pods.
  It denies pod ingress and all other egress, permits DNS to CoreDNS, and
  permits public IPv4 HTTPS while excluding private, loopback, link-local,
  reserved, and Azure platform address ranges. This is a coarse public-HTTPS
  boundary, not a FQDN allowlist; do not put credentials or sensitive data in
  runner workflows. Run
  `./scripts/configure_azure_runner_network_policy.sh` to apply the policy and
  verify public HTTPS plus blocked metadata, node, and Kubernetes API access
  before dispatching a job.
- Dedicate the VM to this runner service. Do not mount host home directories,
  the Podman/Docker socket, or other host control interfaces into runner pods.
- Enable Kubernetes secret encryption at rest before storing GitHub credentials.
  Keep the controller and runner workloads in separate namespaces.
- Enforce network policy so runner pods can reach required public HTTPS
  services but cannot reach the VM host, Kubernetes API, private LAN, link-local
  addresses, or cloud metadata endpoints.
- Runner pods are ephemeral containers, not independent VMs. Treat workflow
  code as capable of compromising the VM; keep pull requests from forks and
  other untrusted sources on GitHub-hosted runners.

## Azure infrastructure scaffold

The Bicep file is infrastructure-as-code only. The short-lived Azure VM and
network pilot has been deployed after a reviewed `what-if`; k3s is installed
and its node is Ready. The ARC 0.15.1 controller is installed in `arc-systems`,
and k3s secret encryption is enabled with re-encryption complete. Runner
network isolation is applied and verified, and the zero-idle `dotfiles-pilot`
scale set is registered with a Running listener. The test-only secret currently
uses the existing broader GitHub CLI token. The manual smoke workflow has run
successfully on the self-hosted runner, and Azure confirmed its ephemeral pod
was removed. The user chose to keep the pilot online; replace the broad test
credential with repository-scoped GitHub App credentials or a fine-grained PAT
before dispatching another job. The rotation helper supports both; App mode
lets ARC mint short-lived tokens without periodic PAT replacement. The VM,
disk, and public IP remain billable while the pilot is kept online.
Use a dedicated resource group for the pilot. The example parameters must be
supplied locally; never commit an SSH private key, GitHub token, or owner-only
deployment values. Inbound traffic is denied by default; any temporary SSH
exception must be limited to the operator's current `/32` and removed
immediately after secret provisioning.

After installing Azure CLI and signing in, select the intended subscription,
create a dedicated resource group, and review the deployment:

```sh
az login
az account set --subscription <subscription-id>
az group create --name rg-arc-pilot --location northcentralus
az deployment group what-if \
  --resource-group rg-arc-pilot \
  --template-file infra/azure-runner/main.bicep \
  --parameters adminUsername=runneradmin \
    adminSshPublicKey="$(cat ~/.ssh/id_ed25519.pub)"
```

`what-if` previews resource changes; it is not a monetary cost estimate and
does not create the VM. Do not run `az deployment group create` until the user
has reviewed the resource plan and separately confirmed billable provisioning.

On the Fedora host, Azure CLI is run from the official rootless Podman image.
Use the guarded helper to install k3s on the deployed VM through Azure Run
Command. It verifies the owner-only CLI cache, enabled subscription, and VM
power state, then prompts before running the installer with administrator
privileges in the guest. The cache is mounted read-only and copied into
container-local temporary state; concurrent SELinux relabeling is avoided.

```sh
./scripts/install_azure_runner_k3s.sh
```

The script skips reinstalling when the k3s service already exists, waits up to
five minutes for node registration and up to three more for `Ready`, then lists
the node. Review its output before installing ARC. If you already ran an older
version and saw `No resources found`, rerun the updated script and type
`CONTINUE`; it will verify the existing installation rather than reinstall it.

The default size is `Standard_B2als_v2` (2 vCPU, 4 GiB, x64) with a 32-GiB
Standard HDD OS disk. The original `Standard_B2s` size failed Azure preflight
in North Central US because of capacity restrictions. The comparable
`Standard_B2als_v2` is listed as available there. Its current public retail
rate is $0.0376/hour; the VM, disk, and static IPv4 estimate is about
$32.64/month at 730 hours, before bandwidth and other charges. Deallocating the
VM stops compute charges but does not remove the managed disk or public IP
charges. Delete the dedicated resource group after a short pilot to stop those
recurring charges. Azure budgets are alerts, not a hard spending cap.

When deployment is explicitly approved, first review the plan with
`az deployment group what-if`, then create the resources only after confirming
the subscription, region, and cost. The final confirmation is required before
running `az deployment group create`. After use, verify the resource group
contains only pilot resources before deleting it. Keep the Azure subscription
and resource-group identifiers out of tracked files.

## Pilot deployment

The scale-set charts are pinned to ARC `0.15.1`; review and update that pin as
a deliberate change. Run the controller helper from the repository root:

```sh
./scripts/install_azure_runner_arc_controller.sh
```

It enables k3s secrets encryption before any GitHub token is stored, waits for
the node and encryption annotation to recover, rotates keys from the initial
`start` stage (or re-encrypts from `rotate`), and verifies encryption is enabled
with all hashes matching and re-encryption finished. It then installs a
checksum-verified Helm binary and the ARC controller into `arc-systems`. This
installs only the controller; it does not register a repository runner. Do not
store credentials until the helper reports encryption enabled and the
controller deployment available.

Apply the runner-only network policy before installing a scale set or storing
its credential. The helper applies it and verifies enforcement with a labeled
temporary pod: public HTTPS and DNS must work, while requests to the VM,
Kubernetes API, private ranges, and Azure metadata endpoints must fail. It
removes the probe pod after checking.

Choose one authentication method. **GitHub App:** where supported, create and
install an App for the pilot repository with
**Administration: read and write** and **Metadata: read** repository
permissions. Record its App ID and installation ID, generate an RSA private
key, and save that PEM file in an owner-only directory. ARC uses the key to
mint short-lived installation tokens. The helper does not create or install the
App. For this personal-account pilot, confirm App installation compatibility
before selecting this mode. Keep its private key protected; when rotating it,
install a replacement with the helper before revoking the old key in GitHub.

```sh
install -d -m 700 "$HOME/.config/traction-control/arc-runner"
chmod 700 "$HOME/.config/traction-control/arc-runner"
app_key_file="$HOME/.config/traction-control/arc-runner/github-app-private-key.pem"
app_id="YOUR_APP_ID"
installation_id="YOUR_INSTALLATION_ID"
install -m 600 /path/to/downloaded-app-private-key.pem "$app_key_file"
chmod 600 "$app_key_file"
./scripts/rotate_azure_runner_secret.sh --github-app \
  "$app_id" "$installation_id" "$app_key_file"
```

**Fine-grained PAT:** create a token for only the pilot repository with
**Administration: read and write**. The account owner creates the PAT in
GitHub; the rotation helper does not create tokens. Enter it locally into an
owner-only file without placing it in shell history, command arguments,
environment variables, this repository, or Azure Run Command:

```bash
install -d -m 700 "$HOME/.config/traction-control/arc-runner"
chmod 700 "$HOME/.config/traction-control/arc-runner"
token_file="$HOME/.config/traction-control/arc-runner/github-token"
read -r -s -p "Fine-grained GitHub PAT: " runner_pat
printf '\n'
(umask 077; printf '%s' "$runner_pat" > "$token_file")
unset runner_pat
chmod 600 "$token_file"
```

Run the rotation helper and type `ROTATE` after reviewing its target and
temporary SSH source address:

```sh
./scripts/rotate_azure_runner_secret.sh --pat "$token_file"
```

The helper checks that k3s encryption is enabled and fully re-encrypted,
validates the owner-only credential file and Azure CLI cache, opens SSH only
from the current public IPv4, obtains the VM host key through Azure Run
Command, and pins it for SSH. It sends the PAT or App credentials only over
verified SSH stdin and uses `kubectl apply` to replace the encrypted
`runner-auth` Secret in place. It removes its temporary SSH key and rule and
restores the deny-all priority on exit. Review any cleanup error before
continuing. The helper deletes its private temporary copy. Remove the local
PAT file after successful rotation; retain or remove the App key according to
your local key-management practice. Do not dispatch another job while the
broader temporary token remains installed. Do not revoke a general-purpose
local GitHub CLI token; revoke only a dedicated PAT after replacement is
verified or when tearing down the pilot.

Copy `config/github-runners/scale-set-values.yaml.template` to an ignored,
owner-only values file on the VM. Set its repository URL to the pilot repository
and `runnerScaleSetName` to `dotfiles-pilot`, matching the workflow's
`runs-on` label. The template contains no credentials or private repository
identifiers.
Install the scale set:

```sh
helm install private-ephemeral-pilot \
  --namespace arc-runners \
  --create-namespace \
  --version 0.15.1 \
  --values /path/to/owner-only/scale-set-values.yaml \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set
```

Verify the controller, listener, and scale set are healthy before manually
dispatching the smoke workflow on the default branch. Confirm the job is
assigned to the pilot scale set and that its runner pod is deleted after the
job completes. Do not enable the pilot workflow for push, pull-request, or
fork events.

The pilot's manual smoke workflow is merged to the default branch and has
passed once on the self-hosted scale set. For a repeat test, open
**Actions → Self-hosted Runner Smoke Test → Run workflow** on the default
branch. It checks out the repository and runs `bash tests/test_install.sh`.

## Expansion and teardown

After the pilot is reviewed, add repositories deliberately, one at a time. Each
private repository gets its own repository-scoped scale set and credential.
Keep existing hosted checks in place until the self-hosted path has passed its
own smoke test and its workflow trust boundary has been reviewed. Do not enroll
public repositories through the private-repository policy.

To remove a pilot, uninstall its scale-set release, delete its Kubernetes
credential, uninstall the controller only if no other scale sets use it, and
revoke the dedicated PAT or uninstall the GitHub App/revoke its private key.
For the Azure scaffold, also delete the dedicated resource group after
confirming it contains only pilot resources.

## References

- [Actions Runner Controller quickstart](https://docs.github.com/en/actions/tutorials/use-actions-runner-controller/get-started)
- [Authenticating ARC to the GitHub API](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/authenticate-to-the-api)
- [Secure use of self-hosted runners](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#hardening-for-self-hosted-runners)
- [Bicep overview](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/overview)
- [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- [Azure public IP addresses](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/public-ip-addresses)
