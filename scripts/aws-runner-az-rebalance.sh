#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || {
  echo "usage: $0 <verify|suspend>" >&2
  exit 2
}
mode=$1
case "$mode" in verify|suspend) ;; *) echo "mode must be verify or suspend" >&2; exit 2;; esac

: "${EKS_CLUSTER_NAME:?EKS_CLUSTER_NAME is required}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID must identify the target account}"
region=${AWS_REGION:-us-east-1}
[[ "$region" == us-east-1 ]] || { echo "AWS_REGION must be us-east-1" >&2; exit 1; }
[[ "$EKS_CLUSTER_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$ ]] || {
  echo "EKS_CLUSTER_NAME is malformed" >&2
  exit 1
}
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$AWS_ACCOUNT_ID" ]] || {
  echo "AWS account mismatch" >&2
  exit 1
}

mapfile -t nodegroups < <(
  aws eks list-nodegroups --region "$region" --cluster-name "$EKS_CLUSTER_NAME" \
    --query 'nodegroups[]' --output text | tr '\t' '\n' | LC_ALL=C sort
)
[[ ${#nodegroups[@]} -gt 0 ]] || { echo "EKS cluster has no node groups" >&2; exit 1; }

for nodegroup in "${nodegroups[@]}"; do
  [[ "$nodegroup" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$ ]] || {
    echo "node group name is malformed" >&2
    exit 1
  }
  mapfile -t groups < <(
    aws eks describe-nodegroup --region "$region" --cluster-name "$EKS_CLUSTER_NAME" \
      --nodegroup-name "$nodegroup" \
      --query 'nodegroup.resources.autoScalingGroups[].name' --output text \
      | tr '\t' '\n' | LC_ALL=C sort
  )
  [[ ${#groups[@]} -gt 0 ]] || {
    echo "node group $nodegroup has no Auto Scaling Group" >&2
    exit 1
  }
  for group in "${groups[@]}"; do
    [[ "$group" =~ ^eks-[A-Za-z0-9-]+$ ]] || {
      echo "Auto Scaling Group name is malformed" >&2
      exit 1
    }
    if [[ "$mode" == suspend ]]; then
      aws autoscaling suspend-processes --region "$region" \
        --auto-scaling-group-name "$group" --scaling-processes AZRebalance
    fi
    # shellcheck disable=SC2016 # JMESPath requires its backtick literal intact.
    suspended=$(aws autoscaling describe-auto-scaling-groups --region "$region" \
      --auto-scaling-group-names "$group" \
      --query 'AutoScalingGroups[0].SuspendedProcesses[?ProcessName==`AZRebalance`].ProcessName | [0]' \
      --output text)
    [[ "$suspended" == AZRebalance ]] || {
      echo "AZRebalance must remain suspended for $group" >&2
      exit 1
    }
    printf 'AZRebalance suspended for %s (%s)\n' "$nodegroup" "$group"
  done
done
