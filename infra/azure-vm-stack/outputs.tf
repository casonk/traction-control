output "resource_group_name" {
  description = "Dedicated group removed by terraform destroy."
  value       = module.node.resource_group_name
}

output "vm_name" {
  description = "Virtual machine name."
  value       = module.node.vm_name
}

output "vm_size" {
  description = "Azure VM size selected by the caller."
  value       = module.node.vm_size
}

output "public_ip_address" {
  description = "Static outbound address. It is billable until the stack is destroyed."
  value       = module.node.public_ip_address
}
