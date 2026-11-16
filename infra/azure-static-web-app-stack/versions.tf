terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
  }

  backend "local" {}
}

provider "azurerm" {
  features {
    resource_group {
      # Deleting the group is the off switch for the site and anything left beside it.
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
}
