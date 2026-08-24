location                   = "eastus2"
state_resource_group_name  = "rg-actions-runner-tfstate-eus2"
state_storage_account_name = "f5runnerstatee2"
log_analytics_workspace_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-platform-observability-eus2/providers/Microsoft.OperationalInsights/workspaces/f5-platform-logs"
terraform_principal_ids    = ["00000000-0000-0000-0000-000000000000"] # Replace with operator and CI object IDs.
state_allowed_ipv4_cidrs   = ["203.0.113.0/24"]                       # Replace with trusted operator or CI egress CIDRs.

tags = {
  environment = "pilot"
}
