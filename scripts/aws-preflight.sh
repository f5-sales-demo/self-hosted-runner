#!/usr/bin/env bash
set -euo pipefail

[[ $# -le 1 ]] || { echo "usage: $0 [terraform-plan]" >&2; exit 2; }
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID must be the expected account ID}"
[[ "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || { echo "AWS_ACCOUNT_ID must contain twelve digits" >&2; exit 2; }
region=${AWS_REGION:-us-east-1}
[[ "$region" == us-east-1 ]] || { echo "AWS_REGION must be us-east-1" >&2; exit 2; }
candidate_enabled=${TF_VAR_enable_compute_32_vcpu_candidate:-false}
[[ "$candidate_enabled" =~ ^(true|false)$ ]] || {
  echo "TF_VAR_enable_compute_32_vcpu_candidate must be true or false" >&2
  exit 2
}
required_vcpu_quota=615
instance_types=(m6a.xlarge m6a.2xlarge m6a.4xlarge)
if [[ "$candidate_enabled" == true ]]; then
  required_vcpu_quota=655
  instance_types+=(c6a.8xlarge)
fi

for command in aws jq python3 terraform; do command -v "$command" >/dev/null; done
tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT

aws_json() {
  local output=$1
  shift
  if ! aws --region "$region" "$@" --output json >"$output" 2>"$output.err"; then
    sed -n '1,20p' "$output.err" >&2
    return 1
  fi
  jq -e . "$output" >/dev/null
}

aws_json "$tmpdir/caller.json" sts get-caller-identity
[[ "$(jq -er .Account "$tmpdir/caller.json")" == "$AWS_ACCOUNT_ID" ]] || {
  echo "authenticated AWS account does not match AWS_ACCOUNT_ID" >&2
  exit 1
}

aws_json "$tmpdir/addons.json" eks describe-addon-versions --kubernetes-version 1.35
for addon_version in \
  vpc-cni=v1.20.4-eksbuild.2 \
  coredns=v1.12.4-eksbuild.1 \
  kube-proxy=v1.35.0-eksbuild.2 \
  eks-pod-identity-agent=v1.3.8-eksbuild.2; do
  addon=${addon_version%%=*}
  version=${addon_version#*=}
  jq -e --arg addon "$addon" --arg version "$version" \
    '.addons[] | select(.addonName == $addon) | .addonVersions | any(.addonVersion == $version)' \
    "$tmpdir/addons.json" >/dev/null || {
    echo "EKS 1.35 add-on unavailable: $addon $version" >&2
    exit 1
  }
done

aws_json "$tmpdir/ami.json" ec2 describe-images \
  --owners amazon \
  --filters Name=name,Values=amazon-eks-node-al2023-x86_64-standard-1.35-v20260911 Name=state,Values=available
[[ "$(jq '.Images | length' "$tmpdir/ami.json")" -eq 1 ]] || {
  echo "pinned AL2023 release 1.35.7-20260911 is unavailable" >&2
  exit 1
}

for zone in us-east-1a us-east-1b us-east-1c; do
  for instance_type in "${instance_types[@]}"; do
    output="$tmpdir/offering-${zone}-${instance_type}.json"
    aws_json "$output" ec2 describe-instance-type-offerings \
      --location-type availability-zone \
      --filters "Name=location,Values=$zone" "Name=instance-type,Values=$instance_type"
    [[ "$(jq '.InstanceTypeOfferings | length' "$output")" -eq 1 ]] || {
      echo "$instance_type is unavailable in $zone" >&2
      exit 1
    }
  done
done

for role in AWSServiceRoleForAmazonEKS AWSServiceRoleForAmazonEKSNodegroup AWSServiceRoleForAutoScaling; do
  aws_json "$tmpdir/role-$role.json" iam get-role --role-name "$role"
done

aws_json "$tmpdir/vcpu-quota.json" service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A
vcpu_quota=$(jq -er '.Quota.Value | floor' "$tmpdir/vcpu-quota.json")
(( vcpu_quota >= required_vcpu_quota )) || {
  echo "standard On-Demand vCPU quota must be at least $required_vcpu_quota; observed $vcpu_quota" >&2
  exit 1
}

aws_json "$tmpdir/eip-quota.json" service-quotas get-service-quota --service-code ec2 --quota-code L-0263D0A3
aws_json "$tmpdir/eips.json" ec2 describe-addresses
aws_json "$tmpdir/vpcs.json" ec2 describe-vpcs
state_root=$(git rev-parse --show-toplevel)/terraform/aws/runner-fleet
if ! terraform -chdir="$state_root" show -json >"$tmpdir/state.json" 2>"$tmpdir/state.err"; then
  sed -n '1,20p' "$tmpdir/state.err" >&2
  exit 1
fi
if [[ ! -s "$tmpdir/state.json" ]]; then
  jq -n '{format_version: "1.0"}' >"$tmpdir/state.json"
fi
jq -e . "$tmpdir/state.json" >/dev/null
scripts/aws-live-preflight.py \
  --eip-quota "$tmpdir/eip-quota.json" \
  --eips "$tmpdir/eips.json" \
  --vpcs "$tmpdir/vpcs.json" \
  --state "$tmpdir/state.json"

if [[ $# -eq 1 ]]; then
  plan_path=$1
  [[ "$plan_path" == /* ]] || plan_path="$(pwd)/$plan_path"
  terraform -chdir="$state_root" show -json "$plan_path" >"$tmpdir/plan.json" 2>"$tmpdir/plan.err" || {
    sed -n '1,20p' "$tmpdir/plan.err" >&2
    exit 1
  }
  jq -e . "$tmpdir/plan.json" >/dev/null
  candidate_args=()
  [[ "$candidate_enabled" == true ]] && candidate_args+=(--candidate-only)
  scripts/aws-plan-preflight.py "${candidate_args[@]}" "$tmpdir/plan.json"
fi

echo "AWS account, region, EKS, quota, address, VPC, and plan gates passed"
