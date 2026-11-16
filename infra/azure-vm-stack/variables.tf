variable "subscription_id" {
  type        = string
  description = "Target subscription. The wrapper supplies this from the active Azure CLI account and does not store it in Git."

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a GUID."
  }
}

variable "resource_group_name" {
  type        = string
  description = "Dedicated resource group for this caller."
}

variable "location" {
  type        = string
  description = "Azure region."
  default     = "northcentralus"
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for this caller's resources."
}

variable "vm_size" {
  type        = string
  description = "Azure VM size, such as Standard_B2als_v2 or Standard_D2s_v5."
}

variable "admin_username" {
  type        = string
  description = "Linux administrator account. Password authentication is disabled."
  default     = "nodeadmin"
}

variable "admin_ssh_public_key" {
  type        = string
  description = "One SSH public key. The wrapper reads it from a local file."
  sensitive   = true
}

variable "address_space" {
  type        = string
  description = "VNet address prefix."
  default     = "10.240.0.0/16"
}

variable "subnet_prefix" {
  type        = string
  description = "Subnet address prefix."
  default     = "10.240.1.0/24"
}

variable "public_tcp_ports" {
  type        = list(number)
  description = "Inbound TCP ports to allow. Empty denies every inbound connection."
  default     = []
}

variable "purpose_tag" {
  type        = string
  description = "Value of the purpose tag."
  default     = "shared-linux-node"
}

variable "lifecycle_tag" {
  type        = string
  description = "Value of the lifecycle tag."
  default     = "destroy-to-stop-billing"
}
