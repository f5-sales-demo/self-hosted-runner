from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "aws_plan_preflight", ROOT / "scripts/aws-plan-preflight.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def plan(
    resource_type="aws_vpc",
    actions=None,
    values=None,
    provider="registry.terraform.io/hashicorp/aws",
):
    return {
        "configuration": {"provider_config": {"aws": {"full_name": provider}}},
        "resource_changes": [
            {
                "address": f"{resource_type}.test",
                "type": resource_type,
                "change": {"actions": actions or ["create"]},
            }
        ],
        "planned_values": values or {},
    }


class AwsPlanPreflightTests(unittest.TestCase):
    def test_accepts_approved_create(self) -> None:
        MODULE.validate_plan(plan())

    def test_ignores_read_only_aws_data_sources(self) -> None:
        value = plan()
        value["resource_changes"].append(
            {
                "address": "data.aws_caller_identity.current",
                "mode": "data",
                "type": "aws_caller_identity",
                "change": {"actions": ["read"]},
            }
        )
        MODULE.validate_plan(value)

    def test_rejects_azure_provider_and_foreign_resources(self) -> None:
        with self.assertRaisesRegex(MODULE.PlanError, "Azure"):
            MODULE.validate_plan(
                plan(provider="registry.terraform.io/hashicorp/azurerm")
            )
        with self.assertRaisesRegex(MODULE.PlanError, "outside"):
            MODULE.validate_plan(plan(resource_type="aws_lambda_function"))

    def test_rejects_delete_replacement_public_and_spot(self) -> None:
        with self.assertRaisesRegex(MODULE.PlanError, "replacement"):
            MODULE.validate_plan(plan(actions=["delete", "create"]))
        with self.assertRaisesRegex(MODULE.PlanError, "public"):
            MODULE.validate_plan(plan(values={"map_public_ip_on_launch": True}))
        with self.assertRaisesRegex(MODULE.PlanError, "Spot"):
            MODULE.validate_plan(plan(values={"capacity_type": "SPOT"}))
        with self.assertRaisesRegex(MODULE.PlanError, "open"):
            MODULE.validate_plan(plan(values={"public_access_cidrs": ["0.0.0.0/0"]}))

    def test_destroy_requires_explicit_mode(self) -> None:
        MODULE.validate_plan(plan(actions=["delete"]), allow_destroy=True)

    def test_rejects_unpinned_node_release_and_image(self) -> None:
        with self.assertRaisesRegex(MODULE.PlanError, "AL2023 release"):
            MODULE.validate_plan(plan(values={"release_version": "latest"}))
        with self.assertRaisesRegex(MODULE.PlanError, "unpinned image"):
            MODULE.validate_plan(
                plan(values={"image": "example.invalid/runner:latest"})
            )
        with self.assertRaisesRegex(MODULE.PlanError, "add-on"):
            MODULE.validate_plan(
                plan(values={"addon_name": "coredns", "addon_version": "latest"})
            )


if __name__ == "__main__":
    unittest.main()
