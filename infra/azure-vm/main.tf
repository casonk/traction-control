locals {
  tags = merge(var.tags, { managedBy = "terraform" })
}

resource "azurerm_resource_group" "node" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_network_security_group" "node" {
  name                = "${var.name_prefix}-nsg"
  location            = azurerm_resource_group.node.location
  resource_group_name = azurerm_resource_group.node.name

  dynamic "security_rule" {
    for_each = { for idx, port in var.public_tcp_ports : tostring(port) => idx }
    content {
      name                       = "AllowTcp${security_rule.key}"
      priority                   = 100 + security_rule.value
      direction                  = "Inbound"
      access                     = "Allow"
      protocol                   = "Tcp"
      source_port_range          = "*"
      destination_port_range     = security_rule.key
      source_address_prefix      = "*"
      destination_address_prefix = "*"
    }
  }

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = length(var.public_tcp_ports) == 0 ? 100 : 4000
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

resource "azurerm_virtual_network" "node" {
  name                = "${var.name_prefix}-vnet"
  location            = azurerm_resource_group.node.location
  resource_group_name = azurerm_resource_group.node.name
  address_space       = [var.address_space]
  tags                = local.tags
}

resource "azurerm_subnet" "node" {
  name                 = "${var.name_prefix}-subnet"
  resource_group_name  = azurerm_resource_group.node.name
  virtual_network_name = azurerm_virtual_network.node.name
  address_prefixes     = [var.subnet_prefix]
}

resource "azurerm_subnet_network_security_group_association" "node" {
  subnet_id                 = azurerm_subnet.node.id
  network_security_group_id = azurerm_network_security_group.node.id
}

resource "azurerm_public_ip" "node" {
  name                    = "${var.name_prefix}-pip"
  location                = azurerm_resource_group.node.location
  resource_group_name     = azurerm_resource_group.node.name
  allocation_method       = "Static"
  sku                     = "Standard"
  ip_version              = "IPv4"
  idle_timeout_in_minutes = 4
  tags                    = local.tags
}

resource "azurerm_network_interface" "node" {
  name                = "${var.name_prefix}-nic"
  location            = azurerm_resource_group.node.location
  resource_group_name = azurerm_resource_group.node.name

  ip_configuration {
    name                          = "node-ipconfig"
    subnet_id                     = azurerm_subnet.node.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.node.id
  }

  tags = local.tags
}

resource "azurerm_linux_virtual_machine" "node" {
  name                            = "${var.name_prefix}-vm"
  computer_name                   = "${var.name_prefix}-vm"
  resource_group_name             = azurerm_resource_group.node.name
  location                        = azurerm_resource_group.node.location
  size                            = var.vm_size
  admin_username                  = var.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.node.id]
  provision_vm_agent              = true
  tags                            = local.tags

  # Azure stores the resolved image build beside version = latest.
  # Following that build would plan a replacement of the running VM.
  lifecycle {
    ignore_changes = [source_image_reference]
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
