terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "local" {}
}

provider "azurerm" {
  features {
    resource_group {
      # Deleting the group is the off switch. Azure keeps the OS disk when only the VM is removed.
      prevent_deletion_if_contains_resources = false
    }
    virtual_machine {
      delete_os_disk_on_deletion = true
    }
  }
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
}
