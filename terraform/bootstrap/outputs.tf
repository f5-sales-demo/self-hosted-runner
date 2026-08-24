output "backend_config" {
  description = "Non-secret azurerm backend coordinates for runner-fleet initialization."
  value = {
    resource_group_name  = azurerm_resource_group.state.name
    storage_account_name = azurerm_storage_account.state.name
    container_name       = azurerm_storage_container.state.name
    key                  = "azure-runner-fleet.tfstate"
    use_azuread_auth     = true
  }
}

output "state_container_name" {
  description = "Private Azure Storage container that holds Terraform state."
  value       = azurerm_storage_container.state.name
}
