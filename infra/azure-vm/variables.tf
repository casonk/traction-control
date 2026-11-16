variable "resource_group_name" {
  type        = string
  description = "Dedicated resource group. Destroying the stack deletes this group."

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must be a single Azure resource group name."
  }
}

variable "location" {
  type        = string
  description = "Azure region."
  default     = "northcentralus"

  validation {
    condition     = can(regex("^[a-z0-9]{1,40}$", var.location))
    error_message = "location must be an Azure region name such as northcentralus."
  }
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for the VM, disk, and network resources."

  validation {
    condition     = can(regex("^[a-z0-9]{3,8}$", var.name_prefix))
    error_message = "name_prefix must be 3 to 8 lowercase letters or digits."
  }
}

variable "vm_size" {
  type        = string
  description = "Azure VM size. The caller chooses it; this module does not assume a size."

  validation {
    condition     = can(regex("^Standard_[A-Za-z0-9_]{1,40}$", var.vm_size))
    error_message = "vm_size must be an Azure size such as Standard_B2als_v2."
  }
}

variable "admin_username" {
  type        = string
  description = "Linux administrator account. Password authentication is disabled."
  default     = "nodeadmin"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.admin_username))
    error_message = "admin_username must be a Linux account name."
  }
}

variable "admin_ssh_public_key" {
  type        = string
  description = "One SSH public key."
  sensitive   = true

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa) AAAA[0-9A-Za-z+/]+={0,3}( .*)?$", var.admin_ssh_public_key))
    error_message = "admin_ssh_public_key must be one ssh-ed25519 or ssh-rsa public key."
  }
}

variable "address_space" {
  type        = string
  description = "VNet address prefix. Use a distinct prefix when networks will be peered."
  default     = "10.240.0.0/16"

  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/[0-9]{1,2}$", var.address_space))
    error_message = "address_space must be an IPv4 CIDR."
  }
}

variable "subnet_prefix" {
  type        = string
  description = "Subnet address prefix inside the VNet."
  default     = "10.240.1.0/24"

  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}/[0-9]{1,2}$", var.subnet_prefix))
    error_message = "subnet_prefix must be an IPv4 CIDR."
  }
}

variable "public_tcp_ports" {
  type        = list(number)
  description = "Inbound TCP ports to allow. Empty denies every inbound connection."
  default     = []

  validation {
    condition = (
      length(var.public_tcp_ports) <= 20 &&
      length(distinct(var.public_tcp_ports)) == length(var.public_tcp_ports) &&
      alltrue([for port in var.public_tcp_ports : port >= 1 && port <= 65535])
    )
    error_message = "public_tcp_ports must be at most 20 distinct TCP ports."
  }
}

variable "tags" {
  type        = map(string)
  description = "Extra tags. managedBy=terraform is always set."
  default     = {}
}
