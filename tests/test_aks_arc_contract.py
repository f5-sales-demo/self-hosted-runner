#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class AksArcContractTests(unittest.TestCase):
    def test_terraform_is_aks_only_and_profile_isolated(self) -> None:
        source = (ROOT / "terraform/runner-fleet/main.tf").read_text(encoding="utf-8")
        for forbidden in (
            "linux_virtual_machine_scale_set",
            "shared_image_gallery",
            "key_vault",
            "cloud-init",
            "role_assignment",
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            'network_plugin_mode = "overlay"',
            'network_data_plane  = "cilium"',
            'network_policy      = "cilium"',
            'node_public_ip_enabled = false',
            '"Standard_D4as_v5"',
            '"Standard_D8ads_v5"',
            '"Standard_D16ads_v5"',
            "only_critical_addons_enabled = true",
            '"runner-profile=${each.value.profile}:NoSchedule"',
            'os_disk_type           = "Ephemeral"',
            'category_group = "allLogs"',
            "oms_agent",
        ):
            self.assertIn(required, source)

    def test_arc_artifacts_and_images_are_pinned(self) -> None:
        controller = (ROOT / "arc/controller-values.yaml").read_text(encoding="utf-8")
        build = (ROOT / "arc/container-build-values.yaml").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts/arc-deploy.sh").read_text(encoding="utf-8")
        self.assertIn('tag: "0.14.2"', controller)
        self.assertRegex(deploy, r"controller_chart_digest=sha256:[0-9a-f]{64}")
        self.assertRegex(deploy, r"scale_set_chart_digest=sha256:[0-9a-f]{64}")
        self.assertRegex(build, r"docker\.io/library/docker@sha256:[0-9a-f]{64}")
        self.assertIn("RUNNER_IMAGE_REQUIRED", build)
        self.assertIn("minRunners: 0", build)
        self.assertIn("emptyDir:", build)

    def test_pilot_targets_only_named_scale_sets(self) -> None:
        workflow = (ROOT / ".github/workflows/arc-pilot.yml").read_text(encoding="utf-8")
        self.assertEqual(
            1, len(re.findall(r"runs-on: self-hosted-runner-socketless", workflow))
        )
        self.assertEqual(
            2,
            len(re.findall(r"runs-on: self-hosted-runner-container-build", workflow)),
        )
        self.assertIn("verify-runner-tools standard", workflow)
        self.assertIn("verify-runner-tools container-build", workflow)
        self.assertIn("docker buildx build", workflow)
        self.assertIn("prior runner pod's Docker cache survived", workflow)


if __name__ == "__main__":
    unittest.main()
