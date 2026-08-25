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


variable "state_allowed_ipv4_cidrs" {
  description = "Trusted Terraform operator or CI egress IPv4 CIDRs allowed to reach the state endpoint."
  type        = set(string)

  validation {
    condition     = alltrue([for cidr in var.state_allowed_ipv4_cidrs : can(cidrnetmask(cidr)) && can(regex("/(0|[1-9]|[12][0-9]|30)$", cidr))])
    error_message = "state_allowed_ipv4_cidrs must contain valid IPv4 CIDRs with prefixes from /0 through /30, as required by Azure Storage firewall rules."
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
