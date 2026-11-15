locals {
  tags = {
    purpose   = "ephemeral-github-runner-pilot"
    managedBy = "terraform"
    lifecycle = "delete-after-pilot"
  }
}

resource "azurerm_resource_group" "pilot" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_network_security_group" "pilot" {
  name                = "${var.name_prefix}-nsg"
  location            = azurerm_resource_group.pilot.location
  resource_group_name = azurerm_resource_group.pilot.name

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

resource "azurerm_virtual_network" "pilot" {
  name                = "${var.name_prefix}-vnet"
  location            = azurerm_resource_group.pilot.location
  resource_group_name = azurerm_resource_group.pilot.name
  address_space       = ["10.240.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "pilot" {
  name                 = "${var.name_prefix}-subnet"
  resource_group_name  = azurerm_resource_group.pilot.name
  virtual_network_name = azurerm_virtual_network.pilot.name
  address_prefixes     = ["10.240.1.0/24"]
}

resource "azurerm_subnet_network_security_group_association" "pilot" {
  subnet_id                 = azurerm_subnet.pilot.id
  network_security_group_id = azurerm_network_security_group.pilot.id
}

resource "azurerm_public_ip" "pilot" {
  name                    = "${var.name_prefix}-pip"
  location                = azurerm_resource_group.pilot.location
  resource_group_name     = azurerm_resource_group.pilot.name
  allocation_method       = "Static"
  sku                     = "Standard"
  ip_version              = "IPv4"
  idle_timeout_in_minutes = 4
  tags                    = local.tags
}

resource "azurerm_network_interface" "pilot" {
  name                = "${var.name_prefix}-nic"
  location            = azurerm_resource_group.pilot.location
  resource_group_name = azurerm_resource_group.pilot.name

  ip_configuration {
    name                          = "runner-ipconfig"
    subnet_id                     = azurerm_subnet.pilot.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.pilot.id
  }

  tags = local.tags
}

resource "azurerm_linux_virtual_machine" "pilot" {
  name                            = "${var.name_prefix}-vm"
  computer_name                   = "${var.name_prefix}-vm"
  resource_group_name             = azurerm_resource_group.pilot.name
  location                        = azurerm_resource_group.pilot.location
  size                            = "Standard_B2als_v2"
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.pilot.id]
  provision_vm_agent              = true
  tags                            = local.tags

  # Azure stores the resolved image build beside version = latest, and the
  # deployed public key includes a trailing space. Following either would
  # plan a change to the running VM.
  lifecycle {
    ignore_changes = [source_image_reference, admin_ssh_key]
  }

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_public_key
  }

  os_disk {
    name                 = "${var.name_prefix}-vm-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 32
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }
}
