output "pool_vmss_ids" {
  description = "VMSS IDs the dispatcher may scale only after policy admission."
  value       = { for name, pool in azurerm_linux_virtual_machine_scale_set.pool : name => pool.id }
}

output "dispatcher_capacity_limits" {
  description = "Maximum VMSS capacities the separately reviewed dispatcher must enforce after admission."
  value = {
    socketless      = var.socketless_max_instances
    container_build = var.container_build_max_instances
  }
}

output "pool_identity_client_ids" {
  description = "Managed identity client IDs for isolated runner pools."
  value       = { for name, identity in azurerm_user_assigned_identity.pool : name => identity.client_id }
}

output "key_vault_id" {
  description = "Key Vault resource ID; secret values are intentionally not Terraform outputs."
  value       = azurerm_key_vault.fleet.id
}

output "log_analytics_workspace_id" {
  value       = azurerm_log_analytics_workspace.fleet.id
  description = "Central diagnostics workspace for the fleet."
}
