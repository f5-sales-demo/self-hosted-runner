#!/usr/bin/env python3
from __future__ import annotations

import json
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
        ):
            self.assertNotIn(forbidden, source)
        for required in (
            'network_plugin_mode = "overlay"',
            'network_data_plane  = "cilium"',
            'network_policy      = "cilium"',
            "node_public_ip_enabled = false",
            '"Standard_D4as_v5"',
            '"Standard_D8ads_v5"',
            '"Standard_D16ads_v5"',
            "only_critical_addons_enabled = true",
            '"runner-profile=${each.value.profile}:NoSchedule"',
            'os_disk_type           = "Ephemeral"',
            'category_group = "allLogs"',
            "oms_agent",
            'name                          = "f5salesdemoarcca"',
            'sku                           = "Premium"',
            "anonymous_pull_enabled        = true",
            'scale_down_unneeded    = "60m"',
        ):
            self.assertIn(required, source)
        self.assertNotIn("azurerm_role_assignment", source)
        self.assertNotIn("AcrPull", source)

    def test_capacity_ceiling_and_quota_arithmetic_are_exact(self) -> None:
        source = (ROOT / "terraform/runner-fleet/main.tf").read_text(
            encoding="utf-8"
        )
        policy = json.loads(
            (ROOT / "config/arc-capacity.json").read_text(encoding="utf-8")
        )
        compute = source.split("compute = {", 1)[1].split("}", 1)[0]
        self.assertIn("minimum      = 0", compute)
        self.assertIn("maximum      = 7", compute)
        self.assertEqual(
            {"socketless": 30, "compute": 7, "container-build": 5},
            policy["pool_capacity"],
        )
        self.assertIn("maximum_runner_vcpus = 30 * 8 + 7 * 16 + 5 * 16", source)
        self.assertIn("maximum_system_vcpus = 3 * 4", source)
        self.assertIn("required_vcpu_quota  = 600", source)
        self.assertEqual(444, 30 * 8 + 7 * 16 + 5 * 16 + 3 * 4)
        self.assertEqual(0.26, 1 - 444 / 600)

    def test_terraform_preserves_autoscaler_owned_node_counts(self) -> None:
        source = (ROOT / "terraform/runner-fleet/main.tf").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ignore_changes = [default_node_pool[0].node_count]", source
        )
        self.assertEqual(2, source.count("ignore_changes = ["))

    def test_arc_artifacts_and_images_are_pinned(self) -> None:
        controller = (ROOT / "arc/controller-values.yaml").read_text(encoding="utf-8")
        build = (ROOT / "arc/container-build-values.yaml").read_text(encoding="utf-8")
        deploy = (ROOT / "scripts/arc-deploy.sh").read_text(encoding="utf-8")
        self.assertIn('tag: "0.14.2"', controller)
        self.assertRegex(deploy, r"controller_chart_digest=sha256:[0-9a-f]{64}")
        self.assertRegex(deploy, r"scale_set_chart_digest=sha256:[0-9a-f]{64}")
        self.assertRegex(build, r"docker\.io/library/docker@sha256:[0-9a-f]{64}")
        self.assertIn("RUNNER_IMAGE_REQUIRED", build)
        self.assertIn("controller|cache|runners|all", deploy)
        self.assertIn("scripts/arc-config.py", deploy)
        self.assertIn("--set-string runnerScaleSetName=", deploy)
        self.assertIn("--set minRunners=", deploy)
        self.assertIn("--set maxRunners=", deploy)
        self.assertNotIn("runnerScaleSetName:", build)
        self.assertNotIn("minRunners:", build)
        self.assertNotIn("maxRunners:", build)
        self.assertIn("emptyDir:", build)
        self.assertIn("name: ghcr-pull", build)
        self.assertIn("kubectl get secret ghcr-pull", deploy)
        self.assertIn("imagePullSecrets[0]=ghcr-pull", deploy)
        self.assertIn("cache_namespace=arc-runner-cache", deploy)
        self.assertEqual(1, deploy.count("for profile in socketless container-build"))
        self.assertIn("nodeProfiles[1]=compute", deploy)
        self.assertIn("scripts/mirror-runner-image.sh verify", deploy)
        self.assertTrue((ROOT / "scripts/arc-copy-pull-secret.sh").stat().st_mode & 0o111)
        mirror = (ROOT / "scripts/mirror-runner-image.sh").read_text(encoding="utf-8")
        self.assertLess(
            mirror.index("az acr login"), mirror.index('if [[ "$mode" == copy ]]')
        )
        self.assertIn('"runner-image-cache-$profile"', deploy)
        prepull = (ROOT / "arc/prepull/templates/daemonset.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("namespace: {{ .Release.Namespace }}", prepull)
        runners_block = deploy.split(
            'if [[ "$mode" == runners || "$mode" == all ]]', 1
        )[1]
        self.assertNotIn("runner-image-cache arc/prepull", runners_block)

    def test_controller_post_renderer_uses_python(self) -> None:
        post_renderer = ROOT / "scripts/arc-controller-post-renderer.py"
        self.assertTrue(post_renderer.stat().st_mode & 0o111)
        self.assertEqual(
            "#!/usr/bin/env python3",
            post_renderer.read_text(encoding="utf-8").splitlines()[0],
        )

    def test_pilot_targets_only_named_scale_sets(self) -> None:
        workflow = (ROOT / ".github/workflows/arc-pilot.yml").read_text(
            encoding="utf-8"
        )
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

    def test_arc_workflow_validates_every_repository_configuration(self) -> None:
        workflow = (ROOT / ".github/workflows/terraform.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("scripts/validate-arc.sh arc/repositories/*.yaml", workflow)


if __name__ == "__main__":
    unittest.main()
