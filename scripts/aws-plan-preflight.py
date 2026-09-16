#!/usr/bin/env python3
"""Fail-closed inspection of an AWS runner-platform Terraform plan JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ALLOWED_TYPES = {
    "aws_autoscaling_group_tag",
    "aws_cloudwatch_log_group",
    "aws_ecr_repository",
    "aws_eip",
    "aws_eks_access_entry",
    "aws_eks_access_policy_association",
    "aws_eks_addon",
    "aws_eks_cluster",
    "aws_eks_node_group",
    "aws_eks_pod_identity_association",
    "aws_flow_log",
    "aws_iam_role",
    "aws_iam_role_policy",
    "aws_iam_role_policy_attachment",
    "aws_internet_gateway",
    "aws_kms_alias",
    "aws_kms_key",
    "aws_launch_template",
    "aws_nat_gateway",
    "aws_route_table",
    "aws_route_table_association",
    "aws_s3_bucket",
    "aws_s3_bucket_policy",
    "aws_s3_bucket_public_access_block",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_versioning",
    "aws_subnet",
    "aws_vpc",
    "aws_cloudtrail",
}
PINNED_ADDONS = {
    "vpc-cni": "v1.20.4-eksbuild.2",
    "coredns": "v1.12.4-eksbuild.1",
    "kube-proxy": "v1.35.0-eksbuild.2",
    "eks-pod-identity-agent": "v1.3.8-eksbuild.2",
}


class PlanError(ValueError):
    pass


def strict_json(path: Path) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PlanError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"invalid Terraform plan JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanError("Terraform plan must be one JSON object")
    return value


def walk(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


def planned_resource_values(module):
    for resource in module.get("resources", []):
        yield resource.get("values", {})
    for child in module.get("child_modules", []):
        yield from planned_resource_values(child)


def validate_plan(plan: dict, *, allow_destroy: bool = False) -> None:
    provider_config = plan.get("configuration", {}).get("provider_config", {})
    provider_names = {
        item.get("full_name", "")
        for item in provider_config.values()
        if isinstance(item, dict)
    }
    if any(name.endswith("/azurerm") for name in provider_names):
        raise PlanError("AWS plan contains an Azure provider")
    if not any(name.endswith("/aws") for name in provider_names):
        raise PlanError("plan does not contain the AWS provider")

    for change in plan.get("resource_changes", []):
        if change.get("mode") == "data":
            continue
        resource_type = change.get("type", "")
        address = change.get("address", "<unknown>")
        actions = change.get("change", {}).get("actions", [])
        if resource_type not in ALLOWED_TYPES:
            raise PlanError(f"resource outside approved AWS graph: {address}")
        if "delete" in actions and not allow_destroy:
            raise PlanError(f"delete or replacement is not allowed: {address}")

    root_module = plan.get("planned_values", {}).get("root_module", {})
    for values in planned_resource_values(root_module):
        for item in walk(values):
            if (
                item.get("map_public_ip_on_launch") is True
                or item.get("associate_public_ip_address") is True
            ):
                raise PlanError("public node/subnet addressing is forbidden")
            if "0.0.0.0/0" in item.get("public_access_cidrs", []):
                raise PlanError("the EKS public API cannot be open to the Internet")
            if item.get("capacity_type") == "SPOT":
                raise PlanError("Spot node capacity is forbidden")
            if (
                "ami_type" in item
                and item["ami_type"] != "AL2023_x86_64_STANDARD"
            ):
                raise PlanError("node groups must use pinned AL2023 images")
            if (
                "release_version" in item
                and item["release_version"] != "1.35.7-20260911"
            ):
                raise PlanError(
                    "node groups must use AL2023 release 1.35.7-20260911"
                )
            addon_name = item.get("addon_name")
            if (
                addon_name in PINNED_ADDONS
                and item.get("addon_version") != PINNED_ADDONS[addon_name]
            ):
                raise PlanError(f"managed add-on is not pinned: {addon_name}")
            for key in ("image", "image_uri"):
                image = item.get(key)
                if isinstance(image, str) and "@sha256:" not in image:
                    raise PlanError(f"unpinned image reference in {key}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan_json", type=Path)
    parser.add_argument("--allow-destroy", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_plan(strict_json(args.plan_json), allow_destroy=args.allow_destroy)
    except PlanError as exc:
        print(f"AWS plan rejected: {exc}", file=sys.stderr)
        return 1
    print("AWS plan passed the exact-scope safety checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
