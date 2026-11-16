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
  description = "Dedicated resource group for this site."
}

variable "location" {
  type        = string
  description = "Region that offers Static Web Apps."
  default     = "centralus"
}

variable "name" {
  type        = string
  description = "Globally unique Static Web App name."
}

variable "sku" {
  type        = string
  description = "Free or Standard."
  default     = "Free"
}

variable "purpose_tag" {
  type        = string
  description = "Value of the purpose tag."
  default     = "static-web-app"
}

variable "lifecycle_tag" {
  type        = string
  description = "Value of the lifecycle tag."
  default     = "free-static-web-app"
}
