variable "location" {
  description = "Azure region for the dedicated state backend."
  type        = string
  default     = "eastus2"
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

variable "terraform_operator_principal_id" {
  description = "Object ID granted Storage Blob Data Contributor on the state container."
  type        = string
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
