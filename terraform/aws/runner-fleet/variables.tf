variable "aws_account_id" {
  description = "Expected twelve-digit AWS account ID."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a twelve-digit account ID."
  }
}

variable "aws_region" {
  description = "Fixed AWS runner-platform region."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "The AWS runner platform is approved only for us-east-1."
  }
}

variable "cluster_name" {
  description = "EKS cluster name."
  type        = string
}

variable "operator_role_arn" {
  description = "Validated AWS IAM Identity Center role ARN granted EKS cluster-admin access."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/", var.operator_role_arn))
    error_message = "operator_role_arn must be an IAM role ARN."
  }
}

variable "operator_ipv4_cidrs" {
  description = "Explicit public IPv4 CIDRs permitted to reach the EKS API."
  type        = set(string)

  validation {
    condition = length(var.operator_ipv4_cidrs) > 0 && alltrue([
      for cidr in var.operator_ipv4_cidrs : can(cidrnetmask(cidr))
    ])
    error_message = "operator_ipv4_cidrs must contain at least one valid CIDR."
  }
}

variable "vpc_cidr" {
  description = "Dedicated, preflight-verified non-overlapping VPC CIDR."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = var.vpc_cidr == "10.42.0.0/16"
    error_message = "The AWS runner VPC is fixed at 10.42.0.0/16."
  }
}

variable "kubernetes_version" {
  description = "Pinned EKS Kubernetes minor version."
  type        = string
  default     = "1.35"

  validation {
    condition     = var.kubernetes_version == "1.35"
    error_message = "The AWS runner platform is pinned to EKS 1.35."
  }
}

variable "node_release_version" {
  description = "Pinned AL2023 EKS optimized AMI release."
  type        = string
  default     = "1.35.7-20260911"

  validation {
    condition     = var.node_release_version == "1.35.7-20260911"
    error_message = "Node groups must use AL2023 release 1.35.7-20260911."
  }
}

variable "addon_versions" {
  description = "Pinned EKS managed add-on versions validated by preflight."
  type        = map(string)
  default = {
    vpc-cni                = "v1.20.4-eksbuild.2"
    coredns                = "v1.12.4-eksbuild.1"
    kube-proxy             = "v1.35.0-eksbuild.2"
    eks-pod-identity-agent = "v1.3.8-eksbuild.2"
  }
}

variable "tags" {
  description = "Tags applied to runner-platform resources."
  type        = map(string)
  default = {
    managed-by = "terraform"
    workload   = "github-actions-runner-platform"
    stage      = "production"
  }
}
