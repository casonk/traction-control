locals {
  tags = merge(var.tags, { managedBy = "terraform" })
}

resource "azurerm_resource_group" "site" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_static_web_app" "site" {
  name                = var.name
  resource_group_name = azurerm_resource_group.site.name
  location            = azurerm_resource_group.site.location

  # Tier and size stay equal. Dedicated is retired and is not a choice here.
  sku_tier = var.sku
  sku_size = var.sku

  configuration_file_changes_enabled = true
  preview_environments_enabled       = true
  public_network_access_enabled      = true

  # No source-control arguments. A token would land in state, and this
  # stack uploads content separately.
  tags = local.tags
}
