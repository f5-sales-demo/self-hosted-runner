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

variable "state_bucket_name" {
  description = "Globally unique S3 bucket for Terraform state."
  type        = string
}

variable "audit_bucket_name" {
  description = "Globally unique S3 bucket for CloudTrail audit logs."
  type        = string
}

variable "tags" {
  description = "Tags applied to bootstrap resources."
  type        = map(string)
  default = {
    managed-by = "terraform"
    workload   = "github-actions-runner-platform"
  }
}
