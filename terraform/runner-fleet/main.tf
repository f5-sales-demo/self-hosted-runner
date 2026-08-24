locals {
  pools = {
    socketless = {
      sku     = var.socketless_vm_sku
      maximum = var.socketless_max_instances
      subnet  = "10.80.1.0/24"
      profile = "socketless"
    }
    container_build = {
      sku     = var.container_build_vm_sku
      maximum = var.container_build_max_instances
      subnet  = "10.80.2.0/24"
      profile = "container-build"
    }
  }
}

resource "azurerm_resource_group" "fleet" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_log_analytics_workspace" "fleet" {
  name                = "${var.name_prefix}-runner-logs"
  location            = azurerm_resource_group.fleet.location
  resource_group_name = azurerm_resource_group.fleet.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_virtual_network" "fleet" {
  name                = "${var.name_prefix}-runner-vnet"
  location            = azurerm_resource_group.fleet.location
  resource_group_name = azurerm_resource_group.fleet.name
  address_space       = ["10.80.0.0/16"]
  tags                = var.tags
}

resource "azurerm_network_security_group" "pool" {
  for_each            = local.pools
  name                = "${var.name_prefix}-${each.key}-nsg"
  location            = azurerm_resource_group.fleet.location
  resource_group_name = azurerm_resource_group.fleet.name
  tags                = merge(var.tags, { pool = each.key })

  security_rule {
    name                       = "deny-internet-inbound"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "Internet"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "deny-virtual-network-inbound"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "allow-https-egress"
    priority                   = 100
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }

  security_rule {
    name                       = "deny-other-internet-egress"
    priority                   = 110
    direction                  = "Outbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }
}

resource "azurerm_subnet" "pool" {
  for_each             = local.pools
  name                 = "${each.key}-runners"
  resource_group_name  = azurerm_resource_group.fleet.name
  virtual_network_name = azurerm_virtual_network.fleet.name
  address_prefixes     = [each.value.subnet]
  service_endpoints    = ["Microsoft.KeyVault", "Microsoft.Storage"]
}

resource "azurerm_subnet_network_security_group_association" "pool" {
  for_each                  = local.pools
  subnet_id                 = azurerm_subnet.pool[each.key].id
  network_security_group_id = azurerm_network_security_group.pool[each.key].id
}

resource "azurerm_user_assigned_identity" "pool" {
  for_each            = local.pools
  name                = "${var.name_prefix}-${each.key}-identity"
  location            = azurerm_resource_group.fleet.location
  resource_group_name = azurerm_resource_group.fleet.name
  tags                = merge(var.tags, { pool = each.key })
}

resource "azurerm_key_vault" "fleet" {
  name                          = "${var.name_prefix}runnerskv"
  location                      = azurerm_resource_group.fleet.location
  resource_group_name           = azurerm_resource_group.fleet.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = true
  tags                          = var.tags

  network_acls {
    bypass                     = "None"
    default_action             = "Deny"
    ip_rules                   = tolist(var.operator_ipv4_cidrs)
    virtual_network_subnet_ids = values(azurerm_subnet.pool)[*].id
  }
}

data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "pool_key_vault" {
  for_each             = local.pools
  scope                = azurerm_key_vault.fleet.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.pool[each.key].principal_id
}

resource "azurerm_linux_virtual_machine_scale_set" "pool" {
  for_each                        = local.pools
  name                            = "${var.name_prefix}-${each.key}-vmss"
  resource_group_name             = azurerm_resource_group.fleet.name
  location                        = azurerm_resource_group.fleet.location
  sku                             = each.value.sku
  instances                       = 0
  admin_username                  = "runneradmin"
  disable_password_authentication = true
  upgrade_mode                    = "Manual"
  overprovision                   = false
  zones                           = ["1", "2", "3"]
  tags                            = merge(var.tags, { pool = each.key, runner-profile = each.value.profile })

  admin_ssh_key {
    username   = "runneradmin"
    public_key = var.admin_ssh_public_key
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
  }

  network_interface {
    name    = "runner"
    primary = true

    ip_configuration {
      name      = "runner"
      primary   = true
      subnet_id = azurerm_subnet.pool[each.key].id
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.pool[each.key].id]
  }

  custom_data = base64encode(<<-CLOUDINIT
    #cloud-config
    write_files:
      - path: /etc/f5-actions-runner/bootstrap.sha256
        permissions: "0600"
        content: "${lower(var.runner_bootstrap_sha256)}  /usr/local/sbin/f5-runner-bootstrap"
    runcmd:
      - ["/usr/bin/curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2", "--retry", "3", "${var.runner_bootstrap_uri}", "-o", "/usr/local/sbin/f5-runner-bootstrap"]
      - ["/usr/bin/sha256sum", "--check", "--status", "/etc/f5-actions-runner/bootstrap.sha256"]
      - ["/usr/bin/chmod", "0700", "/usr/local/sbin/f5-runner-bootstrap"]
      - ["/usr/local/sbin/f5-runner-bootstrap", "${each.value.profile}"]
  CLOUDINIT
  )
}

resource "azurerm_monitor_autoscale_setting" "pool" {
  for_each            = local.pools
  name                = "${var.name_prefix}-${each.key}-capacity"
  resource_group_name = azurerm_resource_group.fleet.name
  location            = azurerm_resource_group.fleet.location
  target_resource_id  = azurerm_linux_virtual_machine_scale_set.pool[each.key].id
  tags                = merge(var.tags, { pool = each.key })

  profile {
    name = "dispatcher-bounded"
    capacity {
      default = 0
      minimum = 0
      maximum = each.value.maximum
    }

  }
}
