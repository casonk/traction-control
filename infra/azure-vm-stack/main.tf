module "node" {
  source = "../azure-vm"

  resource_group_name  = var.resource_group_name
  location             = var.location
  name_prefix          = var.name_prefix
  vm_size              = var.vm_size
  admin_username       = var.admin_username
  admin_ssh_public_key = var.admin_ssh_public_key
  address_space        = var.address_space
  subnet_prefix        = var.subnet_prefix
  public_tcp_ports     = var.public_tcp_ports
  tags = {
    purpose   = var.purpose_tag
    lifecycle = var.lifecycle_tag
  }
}
