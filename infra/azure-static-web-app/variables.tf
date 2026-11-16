variable "resource_group_name" {
  type        = string
  description = "Dedicated resource group. Destroying the stack deletes this group and the site."

  validation {
    condition     = can(regex("^[A-Za-z0-9._()-]{1,90}$", var.resource_group_name))
    error_message = "resource_group_name must be a single Azure resource group name."
  }
}

variable "location" {
  type        = string
  description = "Region that offers Static Web Apps. North Central US does not."
  default     = "centralus"

  validation {
    condition = contains([
      "centralus",
      "eastus2",
      "westus2",
      "westeurope",
      "eastasia",
    ], var.location)
    error_message = "location must be centralus, eastus2, westus2, westeurope, or eastasia."
  }
}

variable "name" {
  type        = string
  description = "Globally unique Static Web App name. This becomes the site, not a secret."

  validation {
    condition     = can(regex("^[0-9A-Za-z]([0-9A-Za-z-]{0,58}[0-9A-Za-z])?$", var.name))
    error_message = "name must be 1 to 60 letters, digits, or interior hyphens."
  }
}

variable "sku" {
  type        = string
  description = "Free or Standard. Tier and size are always the same value."
  default     = "Free"

  validation {
    condition     = contains(["Free", "Standard"], var.sku)
    error_message = "sku must be Free or Standard."
  }
}

variable "tags" {
  type        = map(string)
  description = "Extra tags. managedBy=terraform is always set."
  default     = {}
}
