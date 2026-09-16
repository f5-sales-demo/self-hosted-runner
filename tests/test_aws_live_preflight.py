from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "aws_live_preflight", ROOT / "scripts/aws-live-preflight.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def state(eips: tuple[str, ...] = (), vpc_id: str | None = None, cidr: str = "10.42.0.0/16"):
    resources = [
        {
            "address": f'aws_eip.nat["{index}"]',
            "type": "aws_eip",
            "values": {"allocation_id": allocation_id},
        }
        for index, allocation_id in enumerate(eips)
    ]
    if vpc_id:
        resources.append(
            {
                "address": "aws_vpc.runner",
                "type": "aws_vpc",
                "values": {"id": vpc_id, "cidr_block": cidr},
            }
        )
    return {"values": {"root_module": {"resources": resources}}}


def vpcs(*entries: tuple[str, str]):
    return {
        "Vpcs": [
            {
                "VpcId": vpc_id,
                "CidrBlockAssociationSet": [{"CidrBlock": cidr}],
            }
            for vpc_id, cidr in entries
        ]
    }


class AwsLivePreflightTests(unittest.TestCase):
    quota = {"Quota": {"Value": 8.0}}

    def test_initial_deployment_requires_three_free_eips(self) -> None:
        MODULE.validate_network_capacity(
            self.quota,
            {"Addresses": [{"AllocationId": f"external-{i}"} for i in range(5)]},
            vpcs(("vpc-other", "10.0.0.0/16")),
            state(),
        )
        with self.assertRaisesRegex(MODULE.PreflightError, "capacity for three"):
            MODULE.validate_network_capacity(
                self.quota,
                {"Addresses": [{"AllocationId": f"external-{i}"} for i in range(6)]},
                vpcs(),
                state(),
            )

    def test_recovery_ignores_only_terraform_owned_fleet_eips(self) -> None:
        owned = tuple(f"owned-{i}" for i in range(3))
        addresses = [
            {"AllocationId": allocation_id}
            for allocation_id in (*owned, *(f"external-{i}" for i in range(5)))
        ]
        MODULE.validate_network_capacity(
            self.quota, {"Addresses": addresses}, vpcs(), state(owned)
        )
        with self.assertRaisesRegex(MODULE.PreflightError, "capacity for three"):
            MODULE.validate_network_capacity(
                self.quota,
                {"Addresses": addresses + [{"AllocationId": "unmanaged-extra"}]},
                vpcs(),
                state(owned),
            )

    def test_recovery_allows_only_the_state_owned_target_vpc(self) -> None:
        MODULE.validate_network_capacity(
            self.quota,
            {"Addresses": []},
            vpcs(("vpc-runner", "10.42.0.0/16")),
            state(vpc_id="vpc-runner"),
        )
        for conflicting_vpcs, current_state in (
            (vpcs(("vpc-other", "10.42.0.0/16")), state(vpc_id="vpc-runner")),
            (vpcs(("vpc-runner", "10.42.0.0/16")), state(vpc_id="vpc-runner", cidr="10.42.0.0/17")),
        ):
            with self.assertRaisesRegex(MODULE.PreflightError, "overlaps VPC"):
                MODULE.validate_network_capacity(
                    self.quota, {"Addresses": []}, conflicting_vpcs, current_state
                )


if __name__ == "__main__":
    unittest.main()
