output "state_bucket_name" {
  value       = aws_s3_bucket.state.id
  description = "S3 bucket used by both independent AWS state keys."
}

output "state_kms_key_arn" {
  value       = aws_kms_key.state.arn
  description = "KMS key identifier to place in backend.hcl."
}

output "audit_bucket_name" {
  value       = aws_s3_bucket.audit.id
  description = "S3 bucket receiving CloudTrail management and state-object events."
}

output "cloudtrail_arn" {
  value       = aws_cloudtrail.state.arn
  description = "CloudTrail covering management and Terraform state object events."
}
