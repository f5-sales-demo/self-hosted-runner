variable "location" {
  type        = string
  description = "Azure region for the AKS runner platform."

  validation {
    condition     = lower(var.location) == "canadacentral"
    error_message = "Stage 1 is approved only for Canada Central."
  }
}

variable "resource_group_name" {
  type        = string
  description = "Dedicated resource group for the AKS runner platform."
}

variable "cluster_name" {
  type        = string
  description = "AKS cluster name."
}

variable "cluster_dns_prefix" {
  type        = string
  description = "DNS prefix for the public AKS API endpoint."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,52}[a-z0-9]$", var.cluster_dns_prefix))
    error_message = "cluster_dns_prefix must be a lowercase Azure-compatible DNS prefix."
  }
}

variable "log_analytics_workspace_name" {
  type        = string
  description = "Log Analytics workspace name for AKS diagnostics and Container Insights."
}

variable "kubernetes_version" {
  type        = string
  description = "Exact AKS Kubernetes patch version selected by the availability preflight."
  default     = "1.35.7"

  validation {
    condition     = var.kubernetes_version == "1.35.7"
    error_message = "Stage 1 is pinned to Kubernetes 1.35.7; stop rather than substitute another version."
  }
}

variable "operator_ipv4_cidrs" {
  type        = set(string)
  description = "Explicit public IPv4 CIDRs allowed to reach the AKS API."

  validation {
    condition = length(var.operator_ipv4_cidrs) > 0 && alltrue([
      for cidr in var.operator_ipv4_cidrs :
      can(cidrnetmask(cidr)) && can(regex("^[0-9.]+/(?:[0-9]|[12][0-9]|3[0-2])$", cidr))
    ])
    error_message = "operator_ipv4_cidrs must contain at least one valid IPv4 CIDR."
  }
}

variable "availability_zones" {
  type        = set(string)
  description = "Approved Canada Central availability zones."
  default     = ["1", "2", "3"]

  validation {
    condition     = length(var.availability_zones) == 3 && length(setsubtract(var.availability_zones, toset(["1", "2", "3"]))) == 0
    error_message = "Stage 1 requires Canada Central zones 1, 2, and 3; stop rather than substitute zones."
  }
}

variable "pod_cidr" {
  type        = string
  description = "Non-overlapping private CIDR for Azure CNI Overlay pods."

  validation {
    condition     = can(cidrnetmask(var.pod_cidr))
    error_message = "pod_cidr must be a valid IPv4 CIDR."
  }
}

variable "service_cidr" {
  type        = string
  description = "Non-overlapping private CIDR for Kubernetes services."

  validation {
    condition     = can(cidrnetmask(var.service_cidr))
    error_message = "service_cidr must be a valid IPv4 CIDR."
  }
}

variable "dns_service_ip" {
  type        = string
  description = "Kubernetes DNS service IP contained by service_cidr."

  validation {
    condition     = can(cidrhost("${var.dns_service_ip}/32", 0))
    error_message = "dns_service_ip must be a valid IPv4 address."
  }
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the AKS platform resources."
  default = {
    managed-by = "terraform"
    workload   = "github-actions-runner-platform"
    stage      = "pilot"
  }
}
