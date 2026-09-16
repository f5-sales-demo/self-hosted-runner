data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  contract = jsondecode(file("${path.module}/../../runner-pools.json"))
  zones    = ["us-east-1a", "us-east-1b", "us-east-1c"]
  public_subnets = {
    for index, zone in local.zones : zone => cidrsubnet(var.vpc_cidr, 4, index)
  }
  private_subnets = {
    for index, zone in local.zones : zone => cidrsubnet(var.vpc_cidr, 4, index + 3)
  }
  enabled_runner_pools = {
    for key, pool in local.contract.pools : key => pool
    if pool.enabled && key != "system"
  }
  initial_maximum_vcpus = sum([
    for pool in values(local.contract.pools) : pool.maximum * pool.vcpus if pool.enabled
  ])
  density_maximum_vcpus = sum([
    for pool in values(local.contract.pools) : pool.maximum * pool.vcpus
  ])
  cluster_autoscaler_tags = {
    "k8s.io/cluster-autoscaler/enabled"             = "true"
    "k8s.io/cluster-autoscaler/${var.cluster_name}" = "owned"
  }
  bootstrap_addon_names = toset(["vpc-cni", "eks-pod-identity-agent"])
  bootstrap_addons = {
    for name, version in var.addon_versions : name => version
    if contains(local.bootstrap_addon_names, name)
  }
  node_addons = {
    for name, version in var.addon_versions : name => version
    if !contains(local.bootstrap_addon_names, name)
  }
}

check "authenticated_account" {
  assert {
    condition     = data.aws_caller_identity.current.account_id == var.aws_account_id
    error_message = "AWS credentials do not belong to the explicitly approved account."
  }
}

check "operator_account" {
  assert {
    condition     = startswith(var.operator_role_arn, "arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:role/")
    error_message = "The EKS operator role must belong to the explicitly approved AWS account."
  }
}

check "availability_zones" {
  assert {
    condition     = length(setsubtract(toset(local.zones), toset(data.aws_availability_zones.available.names))) == 0
    error_message = "us-east-1a, us-east-1b, and us-east-1c must all be available."
  }
}

check "capacity_contract" {
  assert {
    condition = (
      local.initial_maximum_vcpus == local.contract.capacity.initial_maximum_vcpus &&
      local.contract.capacity.initial_quota_floor >= ceil(local.initial_maximum_vcpus / (1 - local.contract.capacity.minimum_headroom_ratio)) &&
      local.density_maximum_vcpus == local.contract.capacity.density_enabled_maximum_vcpus &&
      local.contract.capacity.density_enabled_quota_floor >= ceil(local.density_maximum_vcpus / (1 - local.contract.capacity.minimum_headroom_ratio))
    )
    error_message = "Runner-pool capacity must retain 20% quota headroom at 492 vCPUs initially and 652 vCPUs with density enabled."
  }
}

resource "aws_vpc" "runner" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.cluster_name}-vpc" }
}

resource "aws_internet_gateway" "runner" {
  vpc_id = aws_vpc.runner.id
  tags   = { Name = "${var.cluster_name}-igw" }
}

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  vpc_id                  = aws_vpc.runner.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false
  tags = {
    Name                     = "${var.cluster_name}-public-${each.key}"
    "kubernetes.io/role/elb" = "1"
  }
}

resource "aws_subnet" "private" {
  for_each = local.private_subnets

  vpc_id                  = aws_vpc.runner.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = false
  tags = {
    Name                                        = "${var.cluster_name}-private-${each.key}"
    "kubernetes.io/role/internal-elb"           = "1"
    "kubernetes.io/cluster/${var.cluster_name}" = "shared"
  }
}

resource "aws_eip" "nat" {
  for_each = local.public_subnets
  domain   = "vpc"
  tags     = { Name = "${var.cluster_name}-nat-${each.key}" }

  depends_on = [aws_internet_gateway.runner]
}

resource "aws_nat_gateway" "runner" {
  for_each = local.public_subnets

  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.public[each.key].id
  tags          = { Name = "${var.cluster_name}-${each.key}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.runner.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.runner.id
  }
  tags = { Name = "${var.cluster_name}-public" }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  for_each = local.private_subnets
  vpc_id   = aws_vpc.runner.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.runner[each.key].id
  }
  tags = { Name = "${var.cluster_name}-private-${each.key}" }
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private[each.key].id
}

resource "aws_kms_key" "logs" {
  description             = "Encrypted runner-platform CloudWatch logs"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = { ArnLike = { "kms:EncryptionContext:aws:logs:arn" = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:*" } }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/runner-platform/${var.cluster_name}/vpc-flow"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_cloudwatch_log_group" "control_plane" {
  name              = "/aws/eks/${var.cluster_name}/cluster"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.logs.arn
}

resource "aws_iam_role" "flow_logs" {
  name = "${var.cluster_name}-vpc-flow-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "flow_logs" {
  role = aws_iam_role.flow_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.flow.arn}:*"
    }]
  })
}

resource "aws_flow_log" "runner" {
  iam_role_arn    = aws_iam_role.flow_logs.arn
  log_destination = aws_cloudwatch_log_group.flow.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.runner.id
}

resource "aws_kms_key" "eks" {
  description             = "EKS Kubernetes secret envelope encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_iam_role" "cluster" {
  name = "${var.cluster_name}-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "runner" {
  name     = var.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }

  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  encryption_config {
    provider { key_arn = aws_kms_key.eks.arn }
    resources = ["secrets"]
  }

  vpc_config {
    subnet_ids              = values(aws_subnet.private)[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = sort(tolist(var.operator_ipv4_cidrs))
  }

  depends_on = [
    aws_cloudwatch_log_group.control_plane,
    aws_iam_role_policy_attachment.cluster,
  ]
}

resource "aws_eks_access_entry" "operator" {
  cluster_name  = aws_eks_cluster.runner.name
  principal_arn = var.operator_role_arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "operator_admin" {
  cluster_name  = aws_eks_cluster.runner.name
  principal_arn = var.operator_role_arn
  policy_arn    = "arn:${data.aws_partition.current.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
  depends_on = [aws_eks_access_entry.operator]
}

resource "aws_iam_role" "node" {
  name = "${var.cluster_name}-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "AmazonEKSWorkerNodePolicy",
    "AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/${each.value}"
}

resource "aws_launch_template" "node" {
  for_each = merge({ system = local.contract.pools.system }, local.enabled_runner_pools)

  name_prefix = "${var.cluster_name}-${replace(each.value.profile, "_", "-")}-"

  metadata_options {
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      delete_on_termination = true
      encrypted             = true
      volume_size           = 128
      volume_type           = "gp3"
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { runner-profile = each.value.profile }
  }

  update_default_version = true
}

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.runner.name
  node_group_name = "system"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = values(aws_subnet.private)[*].id
  version         = var.kubernetes_version
  release_version = var.node_release_version
  ami_type        = "AL2023_x86_64_STANDARD"
  capacity_type   = "ON_DEMAND"
  instance_types  = [local.contract.pools.system.aws_instance_type]

  launch_template {
    id      = aws_launch_template.node["system"].id
    version = aws_launch_template.node["system"].latest_version
  }
  scaling_config {
    min_size     = local.contract.pools.system.minimum
    max_size     = local.contract.pools.system.maximum
    desired_size = local.contract.pools.system.minimum
  }
  labels = { runner-profile = "system" }
  taint {
    key    = "CriticalAddonsOnly"
    effect = "NO_SCHEDULE"
  }
  update_config { max_unavailable_percentage = 33 }
  tags = local.cluster_autoscaler_tags

  lifecycle { ignore_changes = [scaling_config[0].desired_size] }
  depends_on = [
    aws_iam_role_policy_attachment.node,
    aws_eks_pod_identity_association.vpc_cni,
  ]
}

resource "aws_eks_node_group" "runner" {
  for_each = local.enabled_runner_pools

  cluster_name    = aws_eks_cluster.runner.name
  node_group_name = substr(replace(each.value.profile, "_", "-"), 0, 63)
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = values(aws_subnet.private)[*].id
  version         = var.kubernetes_version
  release_version = var.node_release_version
  ami_type        = "AL2023_x86_64_STANDARD"
  capacity_type   = "ON_DEMAND"
  instance_types  = [each.value.aws_instance_type]

  launch_template {
    id      = aws_launch_template.node[each.key].id
    version = aws_launch_template.node[each.key].latest_version
  }
  scaling_config {
    min_size     = each.value.minimum
    max_size     = each.value.maximum
    desired_size = each.value.minimum
  }
  labels = { runner-profile = each.value.profile }
  taint {
    key    = "runner-profile"
    value  = each.value.profile
    effect = "NO_SCHEDULE"
  }
  update_config { max_unavailable_percentage = 33 }
  tags = merge(local.cluster_autoscaler_tags, { runner-profile = each.value.profile })

  lifecycle { ignore_changes = [scaling_config[0].desired_size] }
  depends_on = [aws_iam_role_policy_attachment.node]
}

resource "aws_autoscaling_group_tag" "system_discovery" {
  for_each               = local.cluster_autoscaler_tags
  autoscaling_group_name = aws_eks_node_group.system.resources[0].autoscaling_groups[0].name
  tag {
    key                 = each.key
    value               = each.value
    propagate_at_launch = false
  }
}

resource "aws_autoscaling_group_tag" "runner_discovery" {
  for_each = {
    for pair in setproduct(keys(local.enabled_runner_pools), keys(local.cluster_autoscaler_tags)) : "${pair[0]}:${pair[1]}" => {
      pool = pair[0]
      key  = pair[1]
    }
  }
  autoscaling_group_name = aws_eks_node_group.runner[each.value.pool].resources[0].autoscaling_groups[0].name
  tag {
    key                 = each.value.key
    value               = local.cluster_autoscaler_tags[each.value.key]
    propagate_at_launch = false
  }
}

resource "aws_iam_role" "pod_identity" {
  for_each = toset(["vpc-cni", "cluster-autoscaler"])
  name     = "${var.cluster_name}-${each.value}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pods.eks.amazonaws.com" }
      Action    = ["sts:AssumeRole", "sts:TagSession"]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "vpc_cni" {
  role       = aws_iam_role.pod_identity["vpc-cni"].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy" "cluster_autoscaler" {
  role = aws_iam_role.pod_identity["cluster-autoscaler"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:DescribeAutoScalingInstances", "autoscaling:DescribeLaunchConfigurations", "autoscaling:DescribeScalingActivities", "ec2:DescribeImages", "ec2:DescribeInstanceTypes", "ec2:DescribeLaunchTemplateVersions", "ec2:GetInstanceTypesFromInstanceRequirements", "eks:DescribeNodegroup"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["autoscaling:SetDesiredCapacity", "autoscaling:TerminateInstanceInAutoScalingGroup"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/k8s.io/cluster-autoscaler/enabled"             = "true"
            "aws:ResourceTag/k8s.io/cluster-autoscaler/${var.cluster_name}" = "owned"
          }
        }
      }
    ]
  })
}

resource "aws_eks_addon" "bootstrap" {
  for_each = local.bootstrap_addons

  cluster_name                = aws_eks_cluster.runner.name
  addon_name                  = each.key
  addon_version               = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"
}

resource "aws_eks_addon" "node" {
  for_each = local.node_addons

  cluster_name                = aws_eks_cluster.runner.name
  addon_name                  = each.key
  addon_version               = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "PRESERVE"

  depends_on = [aws_eks_node_group.system]
}

resource "aws_eks_pod_identity_association" "vpc_cni" {
  cluster_name    = aws_eks_cluster.runner.name
  namespace       = "kube-system"
  service_account = "aws-node"
  role_arn        = aws_iam_role.pod_identity["vpc-cni"].arn
  depends_on      = [aws_eks_addon.bootstrap["eks-pod-identity-agent"]]
}

resource "aws_eks_pod_identity_association" "cluster_autoscaler" {
  cluster_name    = aws_eks_cluster.runner.name
  namespace       = "kube-system"
  service_account = "cluster-autoscaler"
  role_arn        = aws_iam_role.pod_identity["cluster-autoscaler"].arn
  depends_on      = [aws_eks_addon.bootstrap["eks-pod-identity-agent"]]
}

resource "aws_kms_key" "ecr" {
  description             = "ECR image encryption for the runner platform"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_ecr_repository" "images" {
  for_each = toset(["self-hosted-runner", "renovate"])

  name                 = each.value
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }
}
