# Small public website

Copy this shape for one static site on the shared Azure VM module. The
worked example is a sample grocery list. Replace `index.html` before you
publish a real page.

The sample itself no longer runs on a VM. On 2026-11-15 the grocery VM
group was destroyed, and later the same day the Free Static Web App
`casonk-grocery-list` was removed too. Recreate a static site from
`docs/templates/azure-static-web-app/`, not from the VM commands below.

Free includes hosting and a managed certificate that renews itself. There
is no standing compute, disk, or public-IP charge, and no SLA. Microsoft
stops serving a Free site if the subscription exceeds the bandwidth quota.
Static Web Apps is not available in North Central US. Keep the VM shape
below for a workload that needs a machine.

The VM is `Standard_B2ats_v2` in North Central US: 2 vCPU, 1 GiB RAM, and a
20% base CPU. Ubuntu 24.04 is already on the image. The page is served by
`https-server.py`, which uses that Python and does not install a web server
package. Port 80 redirects to HTTPS. Port 443 presents a Let's Encrypt
certificate for the VM's public IP address.

The runner pilot does not use this template. Its inbound policy stays closed.

## Monthly cost

Prices below are public pay-as-you-go retail rates for Linux in North
Central US, checked on 2026-11-15 against the Azure Retail Prices API. They
are not a bill, a reservation, or a spot price. A month is 730 hours.

| Charge | Published rate | Full month |
|---|---:|---:|
| VM `Standard_B2ats_v2`, meter "B2ats v2" | $0.0094 per hour | $6.86 |
| OS disk, 32 GiB Standard HDD, meter "S4 LRS Disk" | $1.536 per month | $1.54 |
| Standard IPv4 static public IP | $0.005 per hour | $3.65 |
| Let's Encrypt certificate | $0 | $0 |
| **Standing total while the group exists** |  | **$12.05** |

These are extra:

- Outbound bandwidth. The first 100 GB each month is $0. Further outbound
  data over the Microsoft network is $0.087 per GB in this region. A page
  of a few kilobytes stays inside the free 100 GB unless something else on
  the VM sends a lot of data.
- Disk operations. The S4 meter is $0.0005 per 10,000 operations.
- A larger VM size. This table is only `Standard_B2ats_v2`.

The disk and the public IP keep billing while the resource group exists,
including while the VM is stopped. `destroy` deletes the group and stops
those charges. The certificate expires about six days after it is issued
(2026-11-21 for the first grocery-list certificate) and must be renewed
before then. Renewal is another Certbot run. It is not a second Azure charge.

## Create the VM

```sh
az login
AZURE_VM_STACK=grocery \
AZURE_VM_SIZE=Standard_B2ats_v2 \
AZURE_VM_PUBLIC_TCP_PORTS=80,443 \
AZURE_VM_ADDRESS_SPACE=10.241.0.0/16 \
AZURE_VM_SUBNET_PREFIX=10.241.1.0/24 \
AZURE_VM_PURPOSE=small-public-website \
  ./scripts/azure_vm_stack.sh apply
```

Type `APPLY` when asked. The stack name must be 3 to 8 lowercase letters or
digits, or you must also set `AZURE_VM_NAME_PREFIX`. State stays in
`~/.local/share/traction-control/azure-vm/grocery`.

`AZURE_VM_PUBLIC_TCP_PORTS` is what opens the site. Leave it unset for any
VM that should stay closed. The runner wrapper clears it.

## Publish the page

Install Certbot 5.4 or newer on the operator machine. Ubuntu's packaged
Certbot is too old for an IP-address certificate.

```sh
python3 -m venv ~/.local/share/azure-website-certbot
~/.local/share/azure-website-certbot/bin/pip install 'certbot>=5.4'
CERTBOT=~/.local/share/azure-website-certbot/bin/certbot \
  ./docs/templates/azure-small-website/publish-https.sh grocery
```

The script requests a shortlived Let's Encrypt certificate for the stack's
public IP, using HTTP-01 on port 80, then copies the certificate and
`https-server.py` onto the VM. The private key stays in the stack's
owner-only state directory. Do not commit it.

Renew before the certificate expires:

```sh
CERTBOT=~/.local/share/azure-website-certbot/bin/certbot \
  ./docs/templates/azure-small-website/publish-https.sh grocery
```

## Remove it

```sh
AZURE_VM_STACK=grocery \
AZURE_VM_SIZE=Standard_B2ats_v2 \
AZURE_VM_PUBLIC_TCP_PORTS=80,443 \
AZURE_VM_ADDRESS_SPACE=10.241.0.0/16 \
AZURE_VM_SUBNET_PREFIX=10.241.1.0/24 \
  ./scripts/azure_vm_stack.sh destroy
```

Type `DESTROY` when asked.

A stack whose ports were opened by hand, outside Terraform, will lose those
ports on the next `apply` unless `AZURE_VM_PUBLIC_TCP_PORTS=80,443` is set
for that apply.
