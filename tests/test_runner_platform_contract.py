from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class RunnerPlatformContractTests(unittest.TestCase):
    def test_shared_capacity_contract(self) -> None:
        contract = json.loads((ROOT / "terraform/runner-pools.json").read_text())
        pools = contract["pools"]
        enabled = sum(
            pool["maximum"] * pool["vcpus"]
            for pool in pools.values()
            if pool["enabled"]
        )
        all_pools = sum(pool["maximum"] * pool["vcpus"] for pool in pools.values())
        self.assertEqual(492, enabled)
        self.assertEqual(652, all_pools)
        self.assertEqual(615, contract["capacity"]["initial_quota_floor"])
        self.assertEqual(815, contract["capacity"]["density_enabled_quota_floor"])
        self.assertFalse(pools["compute_32_vcpu_density_candidate"]["enabled"])
        self.assertEqual(
            "compute-16-vcpu-candidate", pools["compute_16_vcpu_candidate"]["profile"]
        )

    def test_azure_and_aws_are_independent_roots(self) -> None:
        azure = (ROOT / "terraform/azure/runner-fleet/versions.tf").read_text()
        aws = (ROOT / "terraform/aws/runner-fleet/versions.tf").read_text()
        self.assertIn('backend "azurerm"', azure)
        self.assertNotIn('backend "s3"', azure)
        self.assertIn('backend "s3"', aws)
        self.assertNotIn("azurerm", aws)
        self.assertEqual(
            "aws/bootstrap.tfstate",
            self._backend_key("terraform/aws/bootstrap/backend.hcl.example"),
        )
        self.assertEqual(
            "aws/runner-fleet.tfstate",
            self._backend_key("terraform/aws/runner-fleet/backend.hcl.example"),
        )
        for relative in (
            "terraform/aws/bootstrap/backend.hcl.example",
            "terraform/aws/runner-fleet/backend.hcl.example",
        ):
            backend = (ROOT / relative).read_text()
            for forbidden in ("access_key", "secret_key", "profile", "role_arn"):
                self.assertNotIn(forbidden, backend)

    def test_bootstrap_stays_local_until_backend_identifiers_exist(self) -> None:
        helper = (ROOT / "scripts/runner-platform.sh").read_text()
        self.assertIn(
            'if [[ "$stack" == bootstrap && ! -f "$backend" ]]', helper
        )
        self.assertIn(
            'mv -- "$backend_declaration" "$disabled_backend_declaration"', helper
        )
        self.assertIn("trap restore_backend_declaration EXIT", helper)
        self.assertIn('terraform -chdir="$root" init -reconfigure', helper)
        self.assertIn(
            'terraform -chdir="$root" init -migrate-state -force-copy', helper
        )
        self.assertIn(
            'output -json >"$plan_dir/$cloud-$stack.outputs.json"', helper
        )
        self.assertIn(
            'chmod 0600 "$plan_dir/$cloud-$stack.outputs.json"', helper
        )
        self.assertEqual(
            3, helper.count('terraform -chdir="$root" show -json "$plan"')
        )
        self.assertNotIn('      terraform show -json "$plan"', helper)
        readme = (ROOT / "terraform/aws/README.md").read_text()
        self.assertLess(
            readme.index("scripts/runner-platform.sh aws apply"),
            readme.index(
                "cp terraform/aws/bootstrap/backend.hcl.example "
                "terraform/aws/bootstrap/backend.hcl"
            ),
        )

    def test_aws_security_and_node_contract(self) -> None:
        source = (ROOT / "terraform/aws/runner-fleet/main.tf").read_text()
        for required in (
            'capacity_type   = "ON_DEMAND"',
            'ami_type        = "AL2023_x86_64_STANDARD"',
            'http_tokens                 = "required"',
            "volume_size           = 128",
            'volume_type           = "gp3"',
            "endpoint_private_access = true",
            "endpoint_public_access  = true",
            'enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]',
            "retention_in_days = 30",
            'for_each = toset(["self-hosted-runner", "renovate"])',
            'image_tag_mutability = "IMMUTABLE"',
            "scan_on_push = true",
            'bootstrap_addon_names = toset(["vpc-cni", "eks-pod-identity-agent"])',
            "aws_eks_pod_identity_association.vpc_cni,",
            'resource "aws_eks_addon" "bootstrap"',
            'resource "aws_eks_addon" "node"',
        ):
            self.assertIn(required, source)
        self.assertNotIn("SPOT", source)
        self.assertNotIn("remote_access", source)

    def test_aws_autoscaler_matches_kubernetes_minor_and_rbac(self) -> None:
        source = (ROOT / "scripts/aws-addons.sh").read_text()
        self.assertIn("autoscaler_chart_version=9.59.0", source)
        self.assertIn("autoscaler_tag=v1.35.0", source)
        for resource in ("resourceclaims", "resourceslices", "deviceclasses"):
            self.assertIn(resource, source)

    def test_registry_contract_accepts_only_immutable_approved_references(self) -> None:
        prepull = json.loads((ROOT / "arc/prepull/values.schema.json").read_text())
        renovate = json.loads((ROOT / "renovate-system/values.schema.json").read_text())
        for pattern in (
            prepull["properties"]["image"]["pattern"],
            renovate["properties"]["image"]["pattern"],
        ):
            self.assertIn("ghcr", pattern)
            self.assertIn("azurecr", pattern)
            self.assertIn("ecr", pattern)
            self.assertIn("sha256", pattern)

    def test_parallel_qualification_is_serial_by_default_and_evidence_gated(
        self,
    ) -> None:
        contract = json.loads(
            (ROOT / "config/aws-parallel-qualification.json").read_text()
        )
        self.assertEqual(
            ("aws", "us-east-1", "m6a.4xlarge"),
            (contract["provider"], contract["region"], contract["instance_type"]),
        )
        self.assertEqual(0, contract["production_workers"])
        self.assertEqual(2, contract["max_concurrency"])
        self.assertFalse(contract["concurrent_flag_allowed"])
        self.assertEqual(
            [0, 2, 4], [item["workers"] for item in contract["initial_variants"]]
        )
        self.assertEqual(
            [6, 8], [item["workers"] for item in contract["conditional_variants"]]
        )
        gates = contract["promotion_gates"]
        self.assertEqual(10, gates["matched_comparisons"])
        self.assertEqual(0.2, gates["minimum_median_typescript_improvement"])
        self.assertEqual(0.8, gates["maximum_memory_ratio"])
        self.assertFalse(gates["p95_regression_allowed"])
        self.assertTrue(gates["byte_identical_output"])

    @staticmethod
    def _backend_key(relative: str) -> str:
        for line in (ROOT / relative).read_text().splitlines():
            if line.strip().startswith("key"):
                return line.split("=", 1)[1].strip().strip('"')
        raise AssertionError("backend key missing")


if __name__ == "__main__":
    unittest.main()
