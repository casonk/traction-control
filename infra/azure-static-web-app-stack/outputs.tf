output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = module.site.resource_group_name
}

output "name" {
  description = "Static Web App name."
  value       = module.site.name
}

output "sku" {
  description = "Free or Standard."
  value       = module.site.sku
}

output "default_host_name" {
  description = "Public hostname. The managed certificate is on this name."
  value       = module.site.default_host_name
}
