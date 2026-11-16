# Static Web App

Create one Azure Static Web App with `infra/azure-static-web-app` and
`scripts/azure_static_web_app_stack.sh`. The worked example is the sample
grocery list. Terraform creates the site. It does not upload HTML, and it
does not connect a Git repository.

The sample grocery app was removed on 2026-11-15. Its hostname no longer
serves the page. The sample HTML remains in
`docs/templates/azure-small-website/index.html`. The create commands below
are the shape for a new stack. They are not a live site.

Replace the page in `docs/templates/azure-small-website/index.html` before
publishing a real site. A real grocery list is personal data. Keep it in a
private place and turn on the service's sign-in before it is more than the
sample.

## Another site

Each site needs its own `AZURE_SWA_STACK` and its own state directory. The
live site is the portfolio page.

| | |
|---|---|
| Name | `casonk-portfolio` |
| Resource group | `rg-portfolio` |
| Region | Central US (`centralus`) |
| Plan | Free |
| URL | <https://polite-island-07b7c1a10.5.azurestaticapps.net/> |

The page source stays outside this repository. A public page taken from a
private record stays at résumé length and does not name that
repository here.

## Cost

Checked on 2026-11-15. These are public list prices, not a bill.

| Plan | Standing charge | Bandwidth |
|---|---|---|
| Free | $0 | 100 GB per subscription each month. Past that, the site is not served. |
| Standard | $9 per app per month in Central US and West US 2 | The same 100 GB, then $0.20 per GB |

Free includes the managed certificate, two custom domains, and three preview
environments. It has no SLA. Standard adds the SLA, bandwidth overage, and
more domains. The optional Front Door add-on is a separate hourly charge.
Re-check the Retail Prices API before quoting Standard again. The Dedicated
plan is retired and this template rejects it.

There is no VM, disk, or public-IP charge. Destroying the stack deletes the
hostname. It does not stop a bill that was never there on Free.

## Regions

Static Web Apps is not offered in North Central US. The template accepts
`centralus`, `eastus2`, `westus2`, `westeurope`, and `eastasia`. The default
is `centralus`.

## Create

```sh
az login
AZURE_SWA_STACK=grocery \
AZURE_SWA_NAME=casonk-grocery-list \
AZURE_SWA_RESOURCE_GROUP=rg-grocery-site \
AZURE_SWA_LOCATION=centralus \
AZURE_SWA_SKU=Free \
AZURE_SWA_PURPOSE=grocery-list-demo \
AZURE_SWA_LIFECYCLE=free-static-web-app \
  ./scripts/azure_static_web_app_stack.sh plan
```

Type `APPLY` only when the plan is the change you want. A new stack can omit
`AZURE_SWA_RESOURCE_GROUP`; the group is then `rg-<stack>`. `AZURE_SWA_NAME`
is still required because the name is global.

State stays in `~/.local/share/traction-control/azure-swa/<stack>`. That
state contains the deployment token. Do not commit it, log it, or print it.
The Terraform outputs do not include the token.

Changing the name, region, or resource group replaces the site and issues a
new hostname. The wrapper refuses that plan unless `AZURE_SWA_ALLOW_REPLACE=1`.

## Publish files

Terraform does not copy `index.html`. After the site exists, upload the
directory with Microsoft's Static Sites client and the deployment token from
Azure. Write the token to an owner-only file, pass it in the environment,
and delete the file when the upload finishes. Do not put the token in the
shell history or in Git.

## Destroy

```sh
AZURE_SWA_STACK=grocery \
AZURE_SWA_NAME=casonk-grocery-list \
AZURE_SWA_RESOURCE_GROUP=rg-grocery-site \
  ./scripts/azure_static_web_app_stack.sh destroy
```

Type `DESTROY`. This removes the resource group, the site, and the public
hostname.
