output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = azurerm_resource_group.node.name
}

output "vm_name" {
  description = "Virtual machine name."
  value       = azurerm_linux_virtual_machine.node.name
}

output "vm_size" {
  description = "Azure VM size selected by the caller."
  value       = azurerm_linux_virtual_machine.node.size
}

output "public_ip_address" {
  description = "Static outbound address. It is billable until the stack is destroyed."
  value       = azurerm_public_ip.node.ip_address
}
