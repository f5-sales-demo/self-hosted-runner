variable "location" {
  type        = string
  description = "Azure region for the runner fleet."
  default     = "eastus2"
}

variable "resource_group_name" {
  type        = string
  description = "Dedicated Azure resource group for the runner fleet."
}

variable "name_prefix" {
  type        = string
  description = "Short globally unique prefix used in resource names."

  validation {
    condition     = can(regex("^[a-z0-9]{3,15}$", var.name_prefix))
    error_message = "name_prefix must contain 3-15 lowercase letters or digits so derived Key Vault names remain valid."
  }
}

variable "admin_ssh_public_key" {
  type        = string
  description = "Break-glass SSH public key; never commit a private key."
}

variable "runner_bootstrap_uri" {
  type        = string
  description = "HTTPS URI for a versioned bootstrap artifact; it must not contain credentials or a query string."

  validation {
    condition     = can(regex("^https://[^?#]+$", var.runner_bootstrap_uri))
    error_message = "runner_bootstrap_uri must be a credential-free HTTPS URI without a query string or fragment."
  }
}

variable "runner_bootstrap_sha256" {
  type        = string
  description = "Expected SHA-256 hex digest for the bootstrap artifact downloaded by each VMSS instance."

  validation {
    condition     = can(regex("^[0-9a-fA-F]{64}$", var.runner_bootstrap_sha256))
    error_message = "runner_bootstrap_sha256 must be a 64-character hexadecimal SHA-256 digest."
  }
}

variable "operator_ipv4_cidrs" {
  type        = set(string)
  description = "Trusted operator or CI egress IPv4 CIDRs allowed to manage Key Vault secrets."

  validation {
    condition     = alltrue([for cidr in var.operator_ipv4_cidrs : can(cidrnetmask(cidr))])
    error_message = "operator_ipv4_cidrs must contain valid IPv4 CIDRs."
  }
}

variable "socketless_gallery_image_version_id" {
  type        = string
  description = "Immutable Azure Compute Gallery image-version resource ID for socketless runners."

  validation {
    condition     = can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Compute/galleries/[^/]+/images/[^/]+/versions/[^/]+$", var.socketless_gallery_image_version_id))
    error_message = "socketless_gallery_image_version_id must be a complete Azure Compute Gallery image-version resource ID."
  }
}

variable "container_build_gallery_image_version_id" {
  type        = string
  description = "Immutable Azure Compute Gallery image-version resource ID for trusted container-build runners."

  validation {
    condition     = can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Compute/galleries/[^/]+/images/[^/]+/versions/[^/]+$", var.container_build_gallery_image_version_id))
    error_message = "container_build_gallery_image_version_id must be a complete Azure Compute Gallery image-version resource ID."
  }
}

variable "socketless_vm_sku" {
  type        = string
  description = "VM SKU for normal socketless runners."
  default     = "Standard_D4s_v5"
}

variable "container_build_vm_sku" {
  type        = string
  description = "VM SKU for trusted container-build runners."
  default     = "Standard_D8s_v5"
}

variable "socketless_max_instances" {
  type        = number
  description = "Dispatcher-enforced maximum socketless VMSS capacity."
  default     = 6

  validation {
    condition     = var.socketless_max_instances >= 0 && var.socketless_max_instances <= 20
    error_message = "socketless_max_instances must be between 0 and 20."
  }
}

variable "container_build_max_instances" {
  type        = number
  description = "Dispatcher-enforced maximum trusted build VMSS capacity."
  default     = 2

  validation {
    condition     = var.container_build_max_instances >= 0 && var.container_build_max_instances <= 5
    error_message = "container_build_max_instances must be between 0 and 5."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to all runner fleet resources."
  default = {
    managed-by = "terraform"
    workload   = "github-actions-runner-fleet"
  }
}
