output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = azurerm_resource_group.pilot.name
}

output "vm_name" {
  description = "Pilot virtual machine name."
  value       = azurerm_linux_virtual_machine.pilot.name
}

output "public_ip_address" {
  description = "Static outbound address. It is billable until the stack is destroyed."
  value       = azurerm_public_ip.pilot.ip_address
}
