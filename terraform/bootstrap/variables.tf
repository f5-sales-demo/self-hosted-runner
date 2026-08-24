variable "location" {
  description = "Azure region for the dedicated state backend."
  type        = string
}

variable "state_resource_group_name" {
  description = "Dedicated resource group name for Terraform state only."
  type        = string
}

variable "state_storage_account_name" {
  description = "Globally unique, lowercase Azure Storage account name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.state_storage_account_name))
    error_message = "Storage account names must contain 3-24 lowercase letters or digits."
  }
}

variable "log_analytics_workspace_id" {
  description = "Resource ID of the existing Log Analytics workspace that receives state Blob audit logs."
  type        = string
}

variable "terraform_principal_ids" {
  description = "Object IDs for the explicitly authorized Terraform operators and CI identities granted Blob Data Contributor on the state container."
  type        = set(string)

  validation {
    condition     = length(var.terraform_principal_ids) > 0 && alltrue([for principal_id in var.terraform_principal_ids : can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", principal_id))])
    error_message = "terraform_principal_ids must contain at least one Azure AD object ID in UUID form."
  }
}

variable "state_allowed_ipv4_cidrs" {
  description = "Trusted Terraform operator or CI egress IPv4 CIDRs allowed to reach the state endpoint."
  type        = set(string)

  validation {
    condition     = alltrue([for cidr in var.state_allowed_ipv4_cidrs : can(cidrnetmask(cidr))])
    error_message = "state_allowed_ipv4_cidrs must contain valid IPv4 CIDRs."
  }
}

variable "tags" {
  description = "Tags applied to all bootstrap resources."
  type        = map(string)
  default = {
    managed-by = "terraform"
    workload   = "github-actions-runner-fleet"
  }
}
