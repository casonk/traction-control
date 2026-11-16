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
  `infra/azure-runner/terraform`. It creates a North Central US Ubuntu VM,
  isolated VNet/subnet, static public IPv4 for outbound runner traffic, and an
  NSG that denies all inbound traffic. It does not install Kubernetes or ARC,
  create credentials, or deploy any Azure resources by itself.
  `infra/azure-runner/main.bicep` declares the same shape.
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

Terraform is the way to create and delete the billable pilot.
`scripts/azure_runner_stack.sh` runs `infra/azure-runner/terraform` with the
active Azure CLI account. `plan` previews the change, `apply` creates the
resource group, and `destroy` deletes that group, including the VM, managed
disk, and static public IP. State is stored under the owner-only XDG state
directory (`~/.local/share/traction-control/azure-runner` unless
`XDG_STATE_HOME` is set). Do not commit state, `.terraform/`, or a copied
subscription id. `adopt` imports an already deployed pilot into that state so
a later `destroy` removes it.

```sh
az login
./scripts/azure_runner_stack.sh plan
./scripts/azure_runner_stack.sh apply    # type APPLY
./scripts/azure_runner_stack.sh destroy  # type DESTROY
```

`plan` is not a monetary cost estimate and does not create the VM. Do not
apply until the resource plan and cost have been reviewed. `apply` shows the
plan first and asks for `APPLY` only when something would change. It refuses
a plan that would replace an existing resource unless
`AZURE_RUNNER_ALLOW_REPLACE=1`. A plan that only updates tags does not need
to be applied before destroy. `adopt` imports the current pilot into local
state and restores that state if an import fails, so a partial import cannot
become a later destroy of the whole group. The default SSH public key file is
`~/.ssh/id_ed25519.pub`; override it with
`AZURE_RUNNER_SSH_PUBLIC_KEY_FILE`. Never pass a private key. Inbound traffic
is denied by default. Any temporary SSH exception must be limited to the
operator's current `/32` and removed immediately after secret provisioning.

The Bicep file remains the reviewed resource shape. Keep the Terraform module
aligned with it: North Central US, `Standard_B2als_v2`, a 32 GiB Standard HDD
OS disk, `10.240.0.0/16`, and a single deny-all inbound rule. Terraform does
not install Kubernetes or ARC. After apply, use the guarded helpers below.
Stopping or deallocating the VM does not stop the disk and public IP charges.

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
charges. The deployed OS disk is set to detach when the VM is deleted, so
removing only the VM leaves the disk billing. `./scripts/azure_runner_stack.sh destroy`
deletes the resource group and stops those recurring charges. Azure budgets
are alerts, not a hard spending cap. Keep the Azure subscription and
resource-group identifiers out of tracked files.

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

## Local K3s runner (debug and trial)

The same ARC charts run on the Fedora K3s node (Cilium) as a trial before the Azure VM carries them. This is a
weaker boundary than the VM design: runner pods share the host kernel. Accept that only for a **private** repository
you own, with one ephemeral runner at a time. Never enroll a public repository or fork pull requests.

- `config/github-runners/k3s/00-namespaces.yaml`: `arc-systems` (controller, listener) and `arc-runners` (job pods),
  both Pod Security `restricted`.
- `config/github-runners/k3s/20-quota.yaml`: at most 4 runner pods at once across all scale sets; extra jobs queue.
- `config/github-runners/k3s/10-cilium-policy.yaml`: runner pods may resolve and reach only GitHub, the Actions
  service hosts, PyPI, Codecov and the artifact blob hosts on 443. The node, the API server, other nodes and
  link-local/metadata addresses are denied outright; the default-deny closes everything else. Runner pods have no
  service account token and no host mounts.
- `scripts/install_k3s_arc_runner.sh controller` installs the pinned controller. `scale-set OWNER/REPO NAME TOKEN_FILE`
  registers one repository-scoped scale set (refuses a public repository); `status` and `remove NAME` complete it.
  The runner image is `localhost:5000/arc-runner:<version>`, built by `scripts/build_k3s_arc_runner_image.sh` from
  `config/github-runners/k3s/runner-image/Containerfile`: the upstream runner plus `shellcheck`, `age`, `ruby` and `jq`.
  A runner pod has no `sudo`, so anything a job needs from the OS must be in that image. `RUNNER_TOOL_CACHE` points at a
  writable path so `actions/setup-python` can install interpreters without root, and an in-memory `/run/user/1001`
  exists because some tests look for a login-style runtime directory.
- Jobs that need macOS or Windows (an `install-check` matrix) cannot run on a Linux pod and stay on hosted runners.
- A repository switches over with the repository variable `CI_RUNS_ON=NAME` once its caller passes
  `runs-on: ${{ vars.CI_RUNS_ON || 'ubuntu-latest' }}` (see the `casonk/.github` README); deleting the variable
  switches it back.

`scripts/verify_k3s_arc_runner.sh` proves the boundary from inside a throwaway pod with a runner's labels and security
context: allowed hosts answer; unlisted hosts and ports, the node, the API server, the LAN gateway, metadata addresses,
other pods and services, and ingress from the node and other pods are all closed; there is no service-account token,
the root filesystem is read-only and `sudo` fails. It exits with the number of failed probes. Run it after any policy,
Cilium or network change, and before enrolling another repository.

The Azure VM keeps `runner-network-policy.yaml` (plain NetworkPolicy, no Cilium) and the Azure helpers above; the
scale-set values and the `runs-on` mechanism are the same in both places.

## Expansion and teardown

After the pilot is reviewed, add repositories deliberately, one at a time. Each
private repository gets its own repository-scoped scale set and credential.
Keep existing hosted checks in place until the self-hosted path has passed its
own smoke test and its workflow trust boundary has been reviewed. Do not enroll
public repositories through the private-repository policy.

To remove a pilot, uninstall its scale-set release, delete its Kubernetes
credential, uninstall the controller only if no other scale sets use it, and
revoke the dedicated PAT or uninstall the GitHub App/revoke its private key.
For the Azure scaffold, run `./scripts/azure_runner_stack.sh destroy` after
confirming the resource group contains only pilot resources.

## References

- [Actions Runner Controller quickstart](https://docs.github.com/en/actions/tutorials/use-actions-runner-controller/get-started)
- [Authenticating ARC to the GitHub API](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/authenticate-to-the-api)
- [Secure use of self-hosted runners](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions#hardening-for-self-hosted-runners)
- [Terraform language](https://developer.hashicorp.com/terraform/language)
- [AzureRM provider 4.81](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs)
- [Bicep overview](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/overview)
- [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- [Azure public IP addresses](https://learn.microsoft.com/en-us/azure/virtual-network/ip-services/public-ip-addresses)
