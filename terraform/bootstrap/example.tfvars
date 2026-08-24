location                        = "eastus2"
state_resource_group_name       = "rg-actions-runner-tfstate-eus2"
state_storage_account_name      = "f5runnerstatee2"
terraform_operator_principal_id = "00000000-0000-0000-0000-000000000000"
state_allowed_ipv4_cidrs        = ["203.0.113.0/24"] # Replace with trusted operator or CI egress CIDRs.

tags = {
  environment = "pilot"
}
