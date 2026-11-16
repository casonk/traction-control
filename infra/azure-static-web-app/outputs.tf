output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = azurerm_resource_group.site.name
}

output "name" {
  description = "Static Web App name."
  value       = azurerm_static_web_app.site.name
}

output "sku" {
  description = "Free or Standard."
  value       = azurerm_static_web_app.site.sku_tier
}

output "default_host_name" {
  description = "Public hostname. The managed certificate is on this name."
  value       = azurerm_static_web_app.site.default_host_name
}
