output "cluster_name" {
  value       = aws_eks_cluster.runner.name
  description = "EKS cluster name used by the kubeconfig action."
}

output "cluster_endpoint" {
  value       = aws_eks_cluster.runner.endpoint
  description = "Restricted EKS API endpoint."
}

output "runner_node_groups" {
  value       = { for key, group in aws_eks_node_group.runner : key => group.node_group_name }
  description = "Enabled EKS node groups keyed by shared pool name."
}

output "ecr_repository_urls" {
  value       = { for key, repository in aws_ecr_repository.images : key => repository.repository_url }
  description = "Private immutable ECR mirrors for runner and Renovate images."
}

output "initial_maximum_vcpus" {
  value       = local.selected_maximum_vcpus
  description = "Maximum selected AWS capacity including system nodes."
}

output "required_vcpu_quota" {
  value       = local.required_vcpu_quota
  description = "Selected standard On-Demand vCPU quota requirement with 20 percent headroom."
}

output "density_candidate_required_vcpu_quota" {
  value       = local.contract.capacity.aws_candidate_quota_floor
  description = "Required quota for the single AWS 32-vCPU candidate node."
}

output "cluster_autoscaler_role_arn" {
  value       = aws_iam_role.pod_identity["cluster-autoscaler"].arn
  description = "Pod Identity role used by the pinned Cluster Autoscaler chart."
}
