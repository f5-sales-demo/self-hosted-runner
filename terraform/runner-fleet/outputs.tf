output "cluster_id" {
  description = "AKS resource ID."
  value       = azurerm_kubernetes_cluster.runner.id
}

output "cluster_name" {
  description = "AKS cluster name used when obtaining an administrator kubeconfig."
  value       = azurerm_kubernetes_cluster.runner.name
}

output "resource_group_name" {
  description = "Resource group containing the AKS platform."
  value       = azurerm_resource_group.runner.name
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace receiving control-plane and Container Insights data."
  value       = azurerm_log_analytics_workspace.runner.id
}

output "runner_node_pool_names" {
  description = "AKS node-pool names keyed by runner profile."
  value       = { for profile, pool in azurerm_kubernetes_cluster_node_pool.runner : profile => pool.name }
}
