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
  description = "Dedicated resource group. Destroying the stack deletes this group."
  default     = "rg-arc-pilot"

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must be a single Azure resource group name."
  }
}

variable "location" {
  type        = string
  description = "Azure region. North Central US is the reviewed region for this pilot."
  default     = "northcentralus"
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for the pilot resources."
  default     = "arcpilot"

  validation {
    condition     = can(regex("^[a-z0-9]{3,8}$", var.name_prefix))
    error_message = "name_prefix must be 3 to 8 lowercase letters or digits."
  }
}

variable "admin_username" {
  type        = string
  description = "Linux administrator account. Password authentication is disabled."
  default     = "runneradmin"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,31}$", var.admin_username))
    error_message = "admin_username must be a Linux account name."
  }
}

variable "admin_ssh_public_key" {
  type        = string
  description = "One SSH public key. The wrapper reads it from a local file."
  sensitive   = true

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa) AAAA[0-9A-Za-z+/]+={0,3}( .*)?$", var.admin_ssh_public_key))
    error_message = "admin_ssh_public_key must be one ssh-ed25519 or ssh-rsa public key."
  }
}
