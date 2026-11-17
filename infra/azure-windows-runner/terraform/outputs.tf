output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = azurerm_resource_group.win.name
}

output "vm_name" {
  description = "Windows runner virtual machine name."
  value       = one(azurerm_windows_virtual_machine.win[*].name)
}

output "public_ip_address" {
  description = "Static outbound address. It is billable until the stack is destroyed."
  value       = one(azurerm_public_ip.win[*].ip_address)
}

output "subnet_id" {
  description = "Subnet the broker puts per-job VMs in."
  value       = azurerm_subnet.win.id
}

output "gallery_name" {
  description = "Compute gallery holding the golden image."
  value       = azurerm_shared_image_gallery.win.name
}

output "image_definition_name" {
  description = "Image definition inside the gallery."
  value       = azurerm_shared_image.win.name
}
