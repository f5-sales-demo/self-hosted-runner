#!/usr/bin/env bash
set -euo pipefail

[[ $# -le 1 ]] || { echo "usage: $0 [terraform-plan]" >&2; exit 2; }
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID must be the expected account ID}"
[[ "$AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || { echo "AWS_ACCOUNT_ID must contain twelve digits" >&2; exit 2; }
region=${AWS_REGION:-us-east-1}
[[ "$region" == us-east-1 ]] || { echo "AWS_REGION must be us-east-1" >&2; exit 2; }

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

aws_json "$tmpdir/ami.json" ssm get-parameter --name /aws/service/eks/optimized-ami/1.35/amazon-linux-2023/x86_64/standard/recommended/release_version
[[ "$(jq -er .Parameter.Value "$tmpdir/ami.json")" == 1.35.7-20260911 ]] || {
  echo "pinned AL2023 release 1.35.7-20260911 is unavailable" >&2
  exit 1
}

for zone in us-east-1a us-east-1b us-east-1c; do
  for instance_type in m6a.xlarge m6a.2xlarge m6a.4xlarge; do
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
(( vcpu_quota >= 615 )) || { echo "standard On-Demand vCPU quota must be at least 615; observed $vcpu_quota" >&2; exit 1; }

aws_json "$tmpdir/eip-quota.json" service-quotas get-service-quota --service-code ec2 --quota-code L-0263D0A3
aws_json "$tmpdir/eips.json" ec2 describe-addresses
eip_quota=$(jq -er '.Quota.Value | floor' "$tmpdir/eip-quota.json")
eip_allocated=$(jq -er '.Addresses | length' "$tmpdir/eips.json")
(( eip_quota >= 8 && eip_quota - eip_allocated >= 3 )) || {
  echo "Elastic IP quota must be at least 8 with three unused slots; quota=$eip_quota allocated=$eip_allocated" >&2
  exit 1
}

aws_json "$tmpdir/vpcs.json" ec2 describe-vpcs
python3 - "$tmpdir/vpcs.json" <<'PY'
import ipaddress
import json
import sys

target = ipaddress.ip_network("10.42.0.0/16")
with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
for vpc in payload["Vpcs"]:
    for association in vpc.get("CidrBlockAssociationSet", []):
        observed = ipaddress.ip_network(association["CidrBlock"])
        if target.overlaps(observed):
            raise SystemExit(f"10.42.0.0/16 overlaps VPC {vpc['VpcId']} CIDR {observed}")
PY

if [[ $# -eq 1 ]]; then
  terraform show -json "$1" >"$tmpdir/plan.json" 2>"$tmpdir/plan.err" || {
    sed -n '1,20p' "$tmpdir/plan.err" >&2
    exit 1
  }
  jq -e . "$tmpdir/plan.json" >/dev/null
  scripts/aws-plan-preflight.py "$tmpdir/plan.json"
fi

echo "AWS account, region, EKS, quota, address, VPC, and plan gates passed"
