module "site" {
  source = "../azure-static-web-app"

  resource_group_name = var.resource_group_name
  location            = var.location
  name                = var.name
  sku                 = var.sku
  tags = {
    purpose   = var.purpose_tag
    lifecycle = var.lifecycle_tag
  }
}
