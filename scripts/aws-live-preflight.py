#!/usr/bin/env python3
"""Validate AWS network capacity without rejecting Terraform-owned resources."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any, Iterator


TARGET_VPC = ipaddress.ip_network("10.42.0.0/16")
REQUIRED_EIPS = 3


class PreflightError(RuntimeError):
    """Raised when live AWS state cannot safely host the runner fleet."""


def _resources(module: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from _resources(child)


def validate_network_capacity(
    quota_payload: dict[str, Any],
    eips_payload: dict[str, Any],
    vpcs_payload: dict[str, Any],
    state_payload: dict[str, Any],
) -> None:
    root = state_payload.get("values", {}).get("root_module", {})
    resources = list(_resources(root))
    managed_eips = {
        resource.get("values", {}).get("allocation_id")
        for resource in resources
        if resource.get("type") == "aws_eip"
        and resource.get("address", "").startswith("aws_eip.nat[")
    }
    managed_eips.discard(None)
    if len(managed_eips) > REQUIRED_EIPS:
        raise PreflightError(
            f"Terraform state contains {len(managed_eips)} NAT EIPs; expected at most {REQUIRED_EIPS}"
        )

    quota = int(float(quota_payload["Quota"]["Value"]))
    allocated = {
        address["AllocationId"]
        for address in eips_payload.get("Addresses", [])
        if address.get("AllocationId")
    }
    external_allocated = allocated - managed_eips
    if quota < 8 or quota - len(external_allocated) < REQUIRED_EIPS:
        raise PreflightError(
            "Elastic IP quota must be at least 8 with capacity for three fleet EIPs; "
            f"quota={quota} external_allocated={len(external_allocated)} "
            f"fleet_managed={len(allocated & managed_eips)}"
        )

    managed_vpcs = {
        resource.get("values", {}).get("id"): resource.get("values", {}).get(
            "cidr_block"
        )
        for resource in resources
        if resource.get("type") == "aws_vpc"
        and resource.get("address") == "aws_vpc.runner"
        and resource.get("values", {}).get("id")
    }
    if len(managed_vpcs) > 1:
        raise PreflightError("Terraform state contains multiple runner VPCs")

    for vpc in vpcs_payload.get("Vpcs", []):
        vpc_id = vpc["VpcId"]
        for association in vpc.get("CidrBlockAssociationSet", []):
            observed = ipaddress.ip_network(association["CidrBlock"])
            if not TARGET_VPC.overlaps(observed):
                continue
            if (
                vpc_id in managed_vpcs
                and managed_vpcs[vpc_id] == str(TARGET_VPC)
                and observed == TARGET_VPC
            ):
                continue
            raise PreflightError(
                f"{TARGET_VPC} overlaps VPC {vpc_id} CIDR {observed}"
            )


def _load(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise PreflightError(f"{path} must contain one JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eip-quota", required=True)
    parser.add_argument("--eips", required=True)
    parser.add_argument("--vpcs", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    try:
        validate_network_capacity(
            _load(args.eip_quota),
            _load(args.eips),
            _load(args.vpcs),
            _load(args.state),
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError, PreflightError) as error:
        parser.exit(1, f"AWS network preflight failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
