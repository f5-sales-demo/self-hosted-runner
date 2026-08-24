resource "azurerm_resource_group" "state" {
  name     = var.state_resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "state" {
  name                              = var.state_storage_account_name
  resource_group_name               = azurerm_resource_group.state.name
  location                          = azurerm_resource_group.state.location
  account_tier                      = "Standard"
  account_replication_type          = "GRS"
  account_kind                      = "StorageV2"
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  shared_access_key_enabled         = false
  public_network_access_enabled     = true
  default_to_oauth_authentication   = true
  local_user_enabled                = false
  infrastructure_encryption_enabled = true
  tags                              = var.tags

  network_rules {
    default_action = "Deny"
    bypass         = ["None"]
    ip_rules       = tolist(var.state_allowed_ipv4_cidrs)
  }

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "terraform_operator" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.terraform_operator_principal_id
}
