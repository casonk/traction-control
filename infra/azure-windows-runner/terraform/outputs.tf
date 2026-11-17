output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = azurerm_resource_group.win.name
}

output "vm_name" {
  description = "Windows runner virtual machine name."
  value       = azurerm_windows_virtual_machine.win.name
}

output "public_ip_address" {
  description = "Static outbound address. It is billable until the stack is destroyed."
  value       = azurerm_public_ip.win.ip_address
}
