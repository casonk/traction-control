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
  default     = "rg-win-runner"

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must be a single Azure resource group name."
  }
}

variable "location" {
  type        = string
  description = "Azure region. Same reviewed region as the Linux runner pool."
  default     = "northcentralus"
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for the Windows runner resources."
  default     = "winrun"

  validation {
    condition     = can(regex("^[a-z0-9]{3,8}$", var.name_prefix))
    error_message = "name_prefix must be 3 to 8 lowercase letters or digits."
  }
}

variable "admin_username" {
  type        = string
  description = "Local administrator account. Its password is generated and kept only in the owner-only Terraform state; no inbound port is open."
  default     = "runneradmin"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{3,19}$", var.admin_username))
    error_message = "admin_username must be 4 to 20 lowercase letters or digits and must not be a reserved name."
  }
}

variable "vm_size" {
  type        = string
  description = "VM size. B2als_v2 (2 vCPU, 4 GiB) is Generation 2 only and supports Windows."
  default     = "Standard_B2als_v2"
}

variable "shutdown_time_utc" {
  type        = string
  description = "Daily auto-shutdown (deallocate) time as HHMM in UTC, so a forgotten VM stops billing for compute. Empty disables it."
  default     = "0800"

  validation {
    condition     = var.shutdown_time_utc == "" || can(regex("^([01][0-9]|2[0-3])[0-5][0-9]$", var.shutdown_time_utc))
    error_message = "shutdown_time_utc must be empty or HHMM."
  }
}
