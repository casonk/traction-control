locals {
  tags = {
    purpose   = "windows-github-runner"
    managedBy = "terraform"
    lifecycle = "delete-when-not-needed"
  }
}

resource "azurerm_resource_group" "win" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

# No inbound rule is allowed. The VM is managed through Azure Run Command and reaches GitHub outbound.
resource "azurerm_network_security_group" "win" {
  name                = "${var.name_prefix}-nsg"
  location            = azurerm_resource_group.win.location
  resource_group_name = azurerm_resource_group.win.name

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  tags = local.tags
}

resource "azurerm_virtual_network" "win" {
  name                = "${var.name_prefix}-vnet"
  location            = azurerm_resource_group.win.location
  resource_group_name = azurerm_resource_group.win.name
  address_space       = ["10.241.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "win" {
  name                 = "${var.name_prefix}-subnet"
  resource_group_name  = azurerm_resource_group.win.name
  virtual_network_name = azurerm_virtual_network.win.name
  address_prefixes     = ["10.241.1.0/24"]
}

resource "azurerm_subnet_network_security_group_association" "win" {
  subnet_id                 = azurerm_subnet.win.id
  network_security_group_id = azurerm_network_security_group.win.id
}

resource "azurerm_public_ip" "win" {
  count = var.enable_template_vm ? 1 : 0

  name                    = "${var.name_prefix}-pip"
  location                = azurerm_resource_group.win.location
  resource_group_name     = azurerm_resource_group.win.name
  allocation_method       = "Static"
  sku                     = "Standard"
  ip_version              = "IPv4"
  idle_timeout_in_minutes = 4
  tags                    = local.tags
}

resource "azurerm_network_interface" "win" {
  count = var.enable_template_vm ? 1 : 0

  name                = "${var.name_prefix}-nic"
  location            = azurerm_resource_group.win.location
  resource_group_name = azurerm_resource_group.win.name

  ip_configuration {
    name                          = "runner-ipconfig"
    subnet_id                     = azurerm_subnet.win.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.win[0].id
  }

  tags = local.tags
}

# The password is never used interactively (nothing listens), but Windows requires one. It lives only in the
# owner-only local state.
resource "random_password" "admin" {
  count = var.enable_template_vm ? 1 : 0

  length           = 32
  special          = true
  override_special = "!#%*-_=+"
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
}

resource "azurerm_windows_virtual_machine" "win" {
  count = var.enable_template_vm ? 1 : 0

  name                  = "${var.name_prefix}-vm"
  computer_name         = "${var.name_prefix}-vm"
  resource_group_name   = azurerm_resource_group.win.name
  location              = azurerm_resource_group.win.location
  size                  = var.vm_size
  admin_username        = var.admin_username
  admin_password        = random_password.admin[0].result
  network_interface_ids = [azurerm_network_interface.win[0].id]
  provision_vm_agent    = true
  patch_mode            = "AutomaticByPlatform"
  tags                  = local.tags

  # Azure stores the resolved image build beside version = latest; following it would plan a change to the
  # running VM.
  lifecycle {
    ignore_changes = [source_image_reference]
  }

  os_disk {
    name                 = "${var.name_prefix}-vm-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 128
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-azure-edition"
    version   = "latest"
  }
}

resource "azurerm_dev_test_global_vm_shutdown_schedule" "win" {
  count                 = var.enable_template_vm && var.shutdown_time_utc != "" ? 1 : 0
  virtual_machine_id    = azurerm_windows_virtual_machine.win[0].id
  location              = azurerm_resource_group.win.location
  enabled               = true
  daily_recurrence_time = var.shutdown_time_utc
  timezone              = "UTC"

  notification_settings {
    enabled = false
  }

  tags = local.tags
}

# The golden image the broker creates its per-job VMs from. Terraform owns the gallery and the image definition; the
# image versions are captured by scripts/bake_azure_windows_image.sh from the template VM.
resource "azurerm_shared_image_gallery" "win" {
  name                = "${var.name_prefix}gallery"
  resource_group_name = azurerm_resource_group.win.name
  location            = azurerm_resource_group.win.location
  description         = "Golden Windows runner images for the broker."
  tags                = local.tags
}

resource "azurerm_shared_image" "win" {
  name                = "windows-runner"
  gallery_name        = azurerm_shared_image_gallery.win.name
  resource_group_name = azurerm_resource_group.win.name
  location            = azurerm_resource_group.win.location
  os_type             = "Windows"
  hyper_v_generation  = "V2"

  identifier {
    publisher = "portfolio"
    offer     = "github-runner"
    sku       = "windows-server-2022"
  }

  tags = local.tags
}

# Premium option (off by default): an empty image definition for per-job Linux runner VMs. Versions are captured
# by whoever enables the option; see the runbook.
resource "azurerm_shared_image" "linux" {
  count               = var.enable_linux_image_definition ? 1 : 0
  name                = "linux-runner"
  gallery_name        = azurerm_shared_image_gallery.win.name
  resource_group_name = azurerm_resource_group.win.name
  location            = azurerm_resource_group.win.location
  os_type             = "Linux"
  hyper_v_generation  = "V2"

  identifier {
    publisher = "portfolio"
    offer     = "github-runner"
    sku       = "ubuntu-24-04"
  }

  tags = local.tags
}

# These resources gained `count`; the moved blocks keep the existing ones instead of replacing them.
moved {
  from = azurerm_public_ip.win
  to   = azurerm_public_ip.win[0]
}

moved {
  from = azurerm_network_interface.win
  to   = azurerm_network_interface.win[0]
}

moved {
  from = random_password.admin
  to   = random_password.admin[0]
}

moved {
  from = azurerm_windows_virtual_machine.win
  to   = azurerm_windows_virtual_machine.win[0]
}
