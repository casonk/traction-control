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
  `arc-runners` namespace and selects every runner pod in the namespace (any scale set).
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

## Azure VM test (2026-11-16)

The whole path was run once on a fresh VM: `azure_runner_stack.sh apply`, `install_azure_runner_k3s.sh`,
`install_azure_runner_arc_controller.sh` (secret encryption on, hashes matching), `configure_azure_runner_network_policy.sh`
(public HTTPS allowed; the API service, the node's kubelet port, the metadata and the wireserver addresses blocked),
`rotate_azure_runner_secret.sh --pat`, then a scale set for one private repository. A CI run for one private
repository dispatched on that scale set passed both Python legs on its `<repository>-azure-runner-*` pods, which were gone afterwards.
Things to know when repeating it:

- The helpers refuse any group- or world-readable file under `~/.azure`, and the Azure CLI writes its command logs
  mode 644; run `chmod -R go-rwx ~/.azure` after a Terraform run.
- The first `install_azure_runner_k3s.sh` run can report a failure after a healthy install; re-run it to verify.
- There is no scale-set helper for the VM. Render `config/github-runners/k3s/scale-set-values.yaml.template` with the
  repository, name and `githubConfigSecret: runner-auth`, use the upstream runner image (the VM has no local registry,
  so `ruby`, `shellcheck` and `age` are missing: publish the runner image to a registry the VM can pull from before
  enrolling repositories that need them), lower the memory limit to fit the VM (2 vCPU, 4 GiB), and `helm upgrade
  --install` it through Azure Run Command.
- The network policy selects every runner pod in `arc-runners`, not one scale-set name.

## Local against Azure VM comparison (2026-11-16)

All 19 private repositories ran `ci.yml` and `secret-scan.yml` on `main` once on the local K3s scale sets and once on
19 scale sets on the 2 vCPU / 4 GiB Azure VM (custom runner image imported into its containerd, pod quota 2, memory
limit 1536Mi). The VM was destroyed afterwards (`azure_runner_stack.sh destroy`, 8 resources, about $1.07 a day while up).

- **Outcome:** every job that ran on a self-hosted runner passed on both. All 74 jobs finished with the same result
  on both sets. The only differing job was a hosted macOS `install-check` job (cancelled on one run, failed on the other).
- **Speed:** over the 43 jobs that passed on both, 2318 s locally against 4052 s on the VM (about 1.7x). Python test jobs
  took 90-130 s locally and 120-230 s on the VM. Gitleaks, shellcheck and the contract tests took about the same on both.
- **Failures common to both:** Three repositories fail within 2-3 s because their hosted
  jobs (`install-check`, and the jobs that call hosted workflows) are blocked by Actions billing. They say nothing about the runners.
- **Image delivery:** the VM has no registry, so the runner image was streamed with `podman save | ssh | k3s ctr images
  import`, using a throwaway key, the VM's own host key (read through Run Command), a temporary allow rule for one
  source IPv4 address and the deny-all rule moved to priority 110 meanwhile. Everything was removed afterwards.
- **Sizing:** 2 pods at a time on 2 vCPU is the limit; a VM of this size is for isolation and cost control, not speed.

## Choosing a pool: local, Azure or hosted

A repository's CI goes where its `CI_RUNS_ON` variable says (see the `casonk/.github` README). Scale sets are named by
pool: `<repo>-ci` on the local K3s node and `<repo>-azure` on the Azure VM. `scripts/runner_pool.py` reads the private
registry and switches repositories without editing any workflow:

```sh
python3 scripts/runner_pool.py status                    # which pool each private repository uses
python3 scripts/runner_pool.py set azure --dry-run       # preview; then run without --dry-run
python3 scripts/runner_pool.py set local --repo NAME
python3 scripts/runner_pool.py set hosted --repo NAME   # removes the variable
python3 scripts/runner_pool.py balance --weights azure=2,local=1
```

`set azure` refuses a repository with no recorded Azure scale set, because its CI would queue forever; the installer
below records them (`runner_pool.py enroll azure`), and `--force` overrides the check. Only private repositories in the
registry are ever touched. GitHub does not spread jobs across pools by itself: each repository points at one pool, so
`balance` splits the repositories, not the jobs.

To stand the Azure pool up again, run these in order (each guarded step asks you to type its confirmation):

1. `./scripts/azure_runner_stack.sh apply` (type `APPLY`), then `chmod -R go-rwx ~/.azure`
2. `./scripts/install_azure_runner_k3s.sh`, then `./scripts/install_azure_runner_arc_controller.sh`
3. `./scripts/configure_azure_runner_network_policy.sh`
4. `./scripts/rotate_azure_runner_secret.sh --pat ~/.config/traction-control/arc-runner/github-token` (type `ROTATE`)
5. `./scripts/install_azure_runner_scale_sets.sh` (type `SCALESETS`): one `<repo>-azure` scale set per private
   repository, with the runner image built on the VM and 2 runner pods at a time. `--dry-run` previews it.
6. `python3 scripts/runner_pool.py set azure`

Destroy the stack with `./scripts/azure_runner_stack.sh destroy` when the credits or the need run out.

### `install-check` on a self-hosted runner

`install-check.yml` in `casonk/.github` reads the caller's `CI_RUNS_ON`. While it is set, a push or pull request
installs on that one Linux runner; the full matrix (hosted Linux, macOS, Windows) runs only on `workflow_dispatch` and
`schedule`. A private repository therefore no longer fails on GitHub-hosted runners it cannot pay for. Tested from a
private repository on 2026-11-17: only the self-hosted legs ran and all passed.

## macOS and Windows runners (research, 2026-11-17)

The self-hosted pools above are Linux pods. A repository that claims macOS or Windows support still needs those legs
somewhere. Nothing below is built; this is what was found.

- **macOS on Azure: not available.** Azure has no macOS VM image or Mac dedicated host (Apple's licence requires Apple
  hardware). The cloud options are elsewhere: AWS EC2 Mac needs a Dedicated Host with a 24-hour minimum allocation, so
  it only pays off if kept busy, and it is not covered by Azure credits; hosted Mac mini rental is cheaper per day
  but is a standing machine. A Mac you already own can run a self-hosted runner (labels such as
  `[self-hosted, macOS, ARM64]`), ideally with throwaway VMs from Tart or Anka. Keep that to a private repository
  and trusted code only.
- **Windows on Azure: works, as an ordinary VM, not as an ARC pod.** ARC scale sets need Windows nodes (AKS), which
  the K3s design does not have. A Windows VM with a self-hosted runner in ephemeral mode (`--ephemeral`) fits. In
  North Central US the Windows rate of `Standard_B2als_v2` is $0.0468 an hour with the licence included, against
  $0.0376 for Linux (Azure Retail Prices API, 2026-11-17), so about $34 a month always on. The cheap pattern is a VM
  that is deallocated when idle and started by the workflow for a scheduled or manual run; disk and IP charges
  continue while it is deallocated.
- **Stack (applied and tested 2026-11-17):** `./scripts/azure_windows_runner_stack.sh plan|apply|destroy` manages a
  separate stack (`infra/azure-windows-runner/terraform`, resource group `rg-win-runner`, its own owner-only state,
  Windows Server 2022 on `Standard_B2als_v2`, 128 GiB SSD, daily 08:00 UTC auto-shutdown, 10 resources). It opens no
  inbound port; the administrator password is generated and stays in the local state. The auto-shutdown needs the
  subscription registered for `Microsoft.DevTestLab` (`az provider register --namespace Microsoft.DevTestLab`, free,
  once); without it `apply` creates the VM and fails on the schedule, and a second `apply` finishes after registering.
  The live test (Azure Run Command) found: Server 2022 build 20348, 2 vCPU, 4 GiB, 113 GiB free, PowerShell 5.1;
  outbound HTTPS to GitHub, PyPI and Codecov works; RDP, WinRM, SMB and SSH time out from the internet; `git`,
  Python, `pwsh` and `winget` are absent; downloading the 99 MiB runner package took 83 s. The VM was deallocated
  afterwards (disk and IP bill about $0.44 a day; `az vm start` or `destroy` to change that).

- **Recommendation:** keep macOS and Windows legs on `workflow_dispatch`/`schedule` (already how `install-check`
  behaves for self-hosted repositories) and run them on hosted runners when billing allows. If a Windows leg must run
  on every pull request, add a second, separately destroyable Terraform stack for one Windows VM and a runner
  registered to it.

Sources: [Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices),
[Azure has no macOS image (Microsoft Q&A)](https://learn.microsoft.com/en-us/answers/a/1191816),
[EC2 Mac and Dedicated Hosts](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/mac-instance-launch.html),
[ephemeral Windows 11 VM on Azure for GitHub Actions](https://paretosecurity.com/blog/ephemeral-windows-11-testing-vm-on-azure/).

## Windows runner broker (built and tested 2026-11-17)

A Windows runner cannot be an ARC scale set, and a personal account has no shared runner pool, so each repository
needs its own registration. The broker keeps every credential on the Linux side and makes the Windows VM disposable:
one fresh VM per job, deleted afterwards. Code: `services/win-broker` (Go, uses GitHub's official
[`actions/scaleset`](https://github.com/actions/scaleset) client, Public Preview).

**How a job runs.**
1. The broker pod (namespace `win-broker` on the Azure Linux VM; no job code runs in it) registers one scale set per
   allowlisted repository, named `<repo>-windows`, and long-polls GitHub for demand: no REST polling, no webhook endpoint.
2. When a job for `runs-on: <repo>-windows` waits, the broker asks GitHub for a single-use, repository-scoped
   just-in-time runner configuration and creates a VM from the golden image with that configuration as its custom data.
   The VM's disk, NIC and public IP are created with delete option "Delete", so deleting the VM removes all of them.
3. A start task baked into the image (SYSTEM, at boot) reads the configuration from `C:\AzureData\CustomData.bin`,
   deletes the file, and runs `run.cmd --jitconfig`. The runner takes one job and de-registers itself.
4. On job completion the broker deletes the VM (retrying), keeping the cap slot taken until the cloud confirms it is
   gone. A **reaper** sweeps every minute: it deletes any broker-tagged VM older than 60 minutes whatever the broker
   believes, retries deletions that did not finish, and reclaims slots whose VM is gone.

**Measured.** Creating the VM took 2 to 3 minutes from the moment the job queued; a package install job took about 2
minutes more; the VM was gone right after. Cost: $0.0468 an hour for the VM plus a prorated 128 GiB SSD and a static IP,
about one to two cents for a four-minute job. The golden image costs a few dollars a month to store.

**Credentials and trust.** The broker reads the existing fine-grained PAT from a Kubernetes Secret copied inside the
cluster from `runner-auth` (it never leaves the VM and never appears in the Azure activity log). For Azure it uses the
Linux VM's system-assigned managed identity with Contributor on `rg-win-runner` only, so no Azure secret is stored. The
runner pods' network policy blocks the metadata address; the broker's policy allows it for that pod alone. Windows VMs
run code from allowlisted private repositories only, have no inbound path (the subnet's security group denies all
inbound), hold no credential beyond the single-use runner configuration, and disappear after one job. The runner runs as
SYSTEM inside the disposable VM. The broker deletes only VMs that carry its ownership tag.

**Guards.** At most 2 VMs at once (a quota guard and a broker cap, 4 at most by validation); a per-repository allowlist
file; private repositories only (the installer refuses a public one); a time limit enforced by the reaper; a dry-run mode
that registers scale sets and logs what it would create without creating anything.

**Operating it.**
- Allowlist: copy `config/github-runners/windows/allowlist.example.txt` to `allowlist.local.txt` (ignored by Git) and
  list `owner/name` per line.
- Golden image: the template VM is **off by default** (`enable_template_vm = false`), so a plain `apply` cannot leave a
  billing VM behind. To rebake: `TF_VAR_enable_template_vm=true ./scripts/azure_windows_runner_stack.sh apply` (creates the
  template VM), `./scripts/bake_azure_windows_image.sh` (type `BAKE`; installs Git for Windows and the pinned runner, both
  verified against their published digests, adds Git's `bin` directories to the PATH so `shell: bash` works, runs
  sysprep and captures a new image version), then a plain `./scripts/azure_windows_runner_stack.sh apply` removes the
  template VM. Rebake for a new runner release; delete old image versions to save storage.
- Deploy: `./scripts/install_azure_win_broker.sh --dry-run-broker` first, then `--live` (type `BROKER` each time). It
  enables the identity and role, builds the image on the VM, copies the token inside the cluster and applies
  `config/github-runners/windows/win-broker.yaml.template`.
- Opt a repository in: `python3 scripts/runner_pool.py windows set --repo NAME` (refused unless it is in the allowlist;
  `windows clear` and `windows status` also exist). This sets `CI_RUNS_ON_WINDOWS`; the shared `install-check` workflow
  then adds a Windows leg on pushes and pull requests, and a workflow can target the scale set directly with
  `runs-on: <repo>-windows`.
- Watch: `kubectl -n win-broker logs deploy/win-broker` on the VM (through Azure Run Command), and
  `az vm list -g rg-win-runner` should normally be empty.

**Known limits.** One broker replica (a second would double the VMs); a job waits 2 to 3 minutes for its VM; the 2 VM
cap queues a third Windows job; the broker has not been tried with a job that runs for hours (the reaper would end it at
60 minutes: raise `WIN_BROKER_TTL` for such a repository); `actions/scaleset` is a Public Preview API and v0.4.0 is
pinned because later versions need a newer Go than the one used to build here.

### Backing up the golden images

The images exist only in the Azure compute gallery (one region, one locally-redundant replica) and the subscription runs
on credits, so `./scripts/backup_azure_runner_images.sh` (type `BACKUP`) keeps a copy that survives the resource group. For
each recorded image version it creates a temporary managed disk, downloads it through a short-lived read-only link that is
never printed or put on a command line (curl reads it from an owner-only file), checks the size and that the file ends
with a VHD footer, records the raw SHA-256, compresses with zstd, **proves the compressed copy decompresses to the same
hash**, then revokes the link and deletes the disk. `--only windows|linux`, `--dest DIR`, `--allow-flash` and `--dry-run` exist.
Output, owner-only: `<image>-<version>.vhd.zst`, `.sha256` (the hash of the raw VHD) and `.manifest.txt` (what the bake
installed). Re-running skips an image whose copy still verifies.

**Where they live: a spinning disk, never SSD or NVMe.** The script writes the whole raw disk (up to 128 GiB) before it
compresses it, which is bulk write traffic that belongs on an HDD. The backups are in
`/mnt/16tb-sata/portfolio-backups/runner-images/` (the 16 TB Seagate; `/mnt/16tb-sata/backups/` is root-owned, so this
user-owned sibling is used), and `~/.local/share/portfolio-backups/runner-images` is a symlink to it so the default path and
older references keep working. The script **refuses** a destination whose physical disk is not rotational (it follows
symlinks and the encryption layer, and treats an unidentifiable disk as not spinning) unless `--allow-flash` is given, and it
refuses any destination inside a Git working tree. On this machine only `/mnt/16tb-sata` (and the 2 TB USB `/mnt/setup`) are
spinning; `/home`, `/mnt/4tb-m2` and `/mnt/4tb-sata` are flash. The HDD copy is a single copy with no redundancy of its
own: it protects against losing the Azure subscription, not against losing that disk.

Cost and limits: a Windows disk is 128 GiB and a Linux disk 30 GiB; the first 100 GB a month leave Azure free and the rest
is about $0.09 a GB. The Windows image's licence is Azure's, so it can only be restored into Azure; the Linux image is
portable with disk conversion. Back up again after every rebake (a new version is a new file; delete old ones by hand).

Restore (outline, **not yet exercised**; practise it once on the Linux image before relying on it): decompress and check the
hash (`zstd -dc FILE.vhd.zst | sha256sum` must match the `.sha256`), create an upload disk (`az disk create --upload-type
Upload --upload-size-bytes <bytes of the VHD>`), grant write access and upload the VHD to it (azcopy, page blob), revoke,
snapshot the disk, then `az sig image-version create --os-snapshot <snapshot>` into the matching definition (the Linux
definition is Trusted Launch), and record the new version id in the state file the installers read.

**Reproducibility.** The Windows bake pins Git for Windows by release tag (`GIT_TAG`, default `v2.56.1.windows.1`, the one in
the current image; the installer's digest is verified and the installed `git --version` must equal the tag) as it already
pins the runner (`RUNNER_VERSION`). Ubuntu's archive does not keep old package versions, so the Linux bake cannot pin them;
instead it writes `/opt/runner-image-manifest.txt` (OS, kernel, runner and the installed versions of the main packages),
and both bakes save their manifest to `~/.local/share/traction-control/azure-windows-runner/manifests/` so a later rebuild
can be compared with an image you still have.

### Premium option: a disposable Linux VM per job (off by default, not deployed)

The broker can also serve a second class of runner for the same repository: one fresh **Linux VM per job**, registered
as `<repo>-linux-vm`. It exists for deployments that want the Windows model's isolation and capabilities on Linux. It is
**not enabled in this portfolio's deployment and is not the recommended default**: the runner pods are the right tool for
almost every Linux job.

| | Runner pod (default) | Per-job Linux VM (premium option) |
|---|---|---|
| Start-up | seconds | 2 to 3 minutes (VM create and boot) |
| Typical job | gitleaks, install and gate checks take 10 to 40 s | the same job is about 5 to 10 times slower end to end |
| Capacity | a few pods on one VM; queues when busy | one VM per job, limited by the broker cap and the Azure core quota |
| Isolation | shared kernel, restricted namespace, egress policy, no service-account token | its own kernel, disk and network identity |
| Can use root, Docker, systemd, nested virtualisation | no (no sudo, no-new-privileges) | yes |
| Cost | the standing VM (about $1.07 a day) | about $0.04 an hour per running VM plus disk, on top of the standing VM |

Use it only where a job truly needs what a pod cannot give (root, Docker or Podman, systemd, a device) or where policy
requires a VM boundary for the code. For plain queueing, raise the per-repository runner limit and the VM size first.

**The image (built and verified 2026-11-17).** The `linux-runner` definition in the same compute gallery (Ubuntu 24.04,
generation 2, **Trusted Launch**) holds a golden image made by `./scripts/bake_azure_linux_image.sh` (type `BAKE`): it
creates a short-lived template VM, installs the tools jobs assume (Git, Docker, jq, ShellCheck, age, Ruby, Python build
tools), a `runner` user with passwordless sudo and Docker access, and the pinned Actions runner in `/opt/actions-runner`
(verified against the digest GitHub publishes), deprovisions and generalizes it, captures a new image version, records its
id in `~/.local/share/traction-control/azure-windows-runner/linux-image-version.txt`, and deletes the template VM. Unless
`--skip-verify`, it then boots a throwaway VM from the new image with the broker's own cloud-init script (with a runner
configuration that cannot register), checks the tools, Docker daemon, sudo and that the runner started, and deletes
that VM. `--verify-only` re-runs just the verification against the recorded version. The broker passes a cloud-init script
as custom data that does `cd /opt/actions-runner` and runs `./run.sh --jitconfig '<single-use config>'` as `runner`, and
refuses any runner configuration that is not plain base64 text, so it cannot inject shell.

Trusted Launch is required by Azure's rules, not chosen: `az vm create` makes Trusted Launch VMs by default, Azure only
captures such a VM into a definition of type Trusted Launch, and a VM created from that image must itself be Trusted
Launch. The broker's Linux VM definition therefore requests Trusted Launch with Secure Boot and vTPM; the Windows path is
unchanged. The Linux definition is kept by default (`enable_linux_image_definition = true`) because a version lives in it;
it costs nothing by itself and does not enable the broker's class. Changing a definition's security type means replacing it
(`AZURE_WINDOWS_RUNNER_ALLOW_REPLACE=1`), which is only possible while it holds no versions.

**Turning the class on.**
1. Make sure a Linux image version is recorded (above); rebake for a new runner release.
2. `./scripts/install_azure_win_broker.sh --live --with-linux-vm` (type `BROKER`). The installer refuses the flag until the
   image id is recorded. Without the flag the manifest sets `WIN_BROKER_LINUX_VM=0`, and only exactly `1` enables it.
3. Target it from a workflow with `runs-on: <repo>-linux-vm`. The same allowlist, global cap (shared with Windows),
   deleting state, reaper and time limit apply, and the reaper handles both classes.

**Status.** The image is built and verified. The broker's Linux class is implemented and unit-tested (class-aware fleet and
scaler, the VM definition including Trusted Launch, the injection guard, the off-by-default configuration and the installer
and Terraform defaults), but it has **not been run live with a real job**, and it is **not enabled in this portfolio's
deployment**. The first live use should be a dry run, then one repository, watching the VM count return to zero.

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
