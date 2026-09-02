#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZERO = "0" * 64
ONE = "1" * 64


class RenovateContractTests(unittest.TestCase):
    @classmethod
    def render(cls):
        return subprocess.run(
            [
                "helm", "template", "renovate", "renovate-system", "--namespace", "renovate-system",
                "--set-string", f"image=f5salesdemoarcca.azurecr.io/renovate@sha256:{ZERO}",
                "--set-string", "githubApp.appId=1",
                "--set-string", "githubApp.installationId=2",
                "--set-string", "githubApp.botId=3",
                "--set-string", "githubApp.botLogin=f5-renovate-aks[bot]",
            ], cwd=ROOT, check=True, text=True, capture_output=True
        ).stdout

    def test_rendered_security_and_key_separation(self):
        rendered = self.render()
        self.assertIn("suspend: false", rendered)
        self.assertIn("activeDeadlineSeconds: 2700", rendered)
        self.assertIn("backoffLimit: 0", rendered)
        self.assertIn("automountServiceAccountToken: false", rendered)
        self.assertIn("readOnlyRootFilesystem: true", rendered)
        self.assertIn("seccompProfile: {type: RuntimeDefault}", rendered)
        self.assertIn("medium: Memory", rendered)
        self.assertRegex(rendered, r"name: renovate-config-[0-9a-f]{16}")
        main = rendered.split("containers:\n", 2)[-1].split("volumes:\n", 1)[0]
        self.assertNotIn("app-key", main)
        self.assertNotIn("private-key.pem", main)
        self.assertNotIn("RENOVATE_GITHUB_APP_ID", main)

    def test_fail_closed_cilium_policy_has_only_dns_and_five_https_hosts(self):
        policy = (ROOT / "renovate-system/templates/networkpolicy.yaml").read_text()
        self.assertIn("ingress: []", policy)
        self.assertIn("k8s:k8s-app: kube-dns", policy)
        self.assertIn('port: "53", protocol: UDP', policy)
        self.assertIn('port: "53", protocol: TCP', policy)
        self.assertEqual(5, len(re.findall(r"matchName:", policy)))
        self.assertEqual(1, len(re.findall(r'port: "443"', policy)))
        self.assertNotIn("toEndpoints: [{}]", policy)

    def test_image_source_and_isolated_context_are_pinned(self):
        source = json.loads((ROOT / "renovate-system/image-source.json").read_text())
        dockerfile = (ROOT / "renovate-system/Dockerfile").read_text()
        self.assertEqual("44.52.1", source["upstream"]["version"])
        self.assertIn(source["upstream"]["image"].replace("renovate/renovate@", "renovate/renovate:44.52.1@"), dockerfile)
        self.assertIn("USER 12021:0", dockerfile)
        self.assertIn("/opt/f5-renovate/containerbase", dockerfile)
        self.assertIn("/opt/f5-renovate/containerbase-runtime", dockerfile)
        self.assertNotIn("USER 1001:1001", dockerfile)
        workflow = (ROOT / ".github/workflows/publish-renovate.yml").read_text()
        self.assertIn("--tag \"$image:$tag\" renovate-system", workflow)
        self.assertIn("attest-build-provenance", workflow)
        verify = (ROOT / ".github/workflows/verify.yml").read_text()
        self.assertIn("scripts/verify-renovate-runtime.sh local/renovate:test", verify)

    def test_promotion_and_deployment_reject_tags_and_non_acr_runtime(self):
        promote = ROOT / "scripts/promote-renovate-image.sh"
        for invalid in ("ghcr.io/f5-sales-demo/renovate:latest", f"docker.io/renovate/renovate@sha256:{ZERO}"):
            result = subprocess.run([promote, invalid, "a" * 40, "/tmp/unused"], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(2, result.returncode)
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertIn("^f5salesdemoarcca\\.azurecr\\.io/renovate@", deploy)
        self.assertNotIn("renovate:latest", deploy)
        self.assertIn("git diff --quiet \"$commit\" --", deploy)
        for runtime_input in (
            "renovate-system/Dockerfile",
            "renovate-system/app-token-init.mjs",
            "renovate-system/github-app.mjs",
            "renovate-system/token-entrypoint.mjs",
            "renovate-system/image-source.json",
        ):
            self.assertIn(runtime_input, deploy)
        promotion = promote.read_text()
        self.assertIn("gh attestation verify", promotion)
        self.assertIn("--deny-self-hosted-runners", promotion)

    def test_socketless_prepull_accepts_only_acr_renovate_digest(self):
        schema = json.loads((ROOT / "arc/prepull/values.schema.json").read_text())
        pattern = re.compile(schema["properties"]["renovateImage"]["pattern"])
        self.assertIsNotNone(pattern.fullmatch(f"f5salesdemoarcca.azurecr.io/renovate@sha256:{ONE}"))
        self.assertIsNone(pattern.fullmatch(f"ghcr.io/f5-sales-demo/renovate@sha256:{ONE}"))
        self.assertIsNone(pattern.fullmatch("f5salesdemoarcca.azurecr.io/renovate:latest"))

    def test_anonymous_acr_pull_has_no_renovate_secret_interface(self):
        schema = json.loads((ROOT / "renovate-system/values.schema.json").read_text())
        self.assertNotIn("imagePullSecrets", schema["required"])
        self.assertNotIn("imagePullSecrets", schema["properties"])
        chart = (ROOT / "renovate-system/templates/cronjob.yaml").read_text()
        self.assertNotIn("imagePullSecrets", chart)
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertNotIn("RENOVATE_ACR_PULL_SECRET", deploy)
        self.assertNotIn("renovate-acr-pull", deploy)
        self.assertIn("imagePullSecrets[0]=ghcr-pull", deploy)
        self.assertIn('.auths | keys == ["ghcr.io"]', deploy)
        helper = ROOT / "scripts/renovate-acr-pull-secret.sh"
        self.assertFalse(helper.exists())

    def test_socketless_prepuller_uses_only_private_ghcr_secret(self):
        schema = json.loads((ROOT / "arc/prepull/values.schema.json").read_text())
        self.assertEqual(["ghcr-pull"], schema["properties"]["imagePullSecrets"]["const"])
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertNotIn("imagePullSecrets[1]", deploy)

    def test_renovate_deployment_timeout_covers_serial_node_rollout(self):
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertIn("deploy_timeout=15m", deploy)
        self.assertEqual(3, deploy.count('"$deploy_timeout"'))
        self.assertNotIn("--timeout 10m", deploy)
        self.assertNotIn("--timeout=10m", deploy)

    def test_token_entrypoint_reuses_structured_installation_token_validator(self):
        entrypoint = (ROOT / "renovate-system/token-entrypoint.mjs").read_text()
        self.assertIn("validateInstallationToken", entrypoint)
        self.assertNotIn("/^ghs_", entrypoint)
        self.assertIn("/opt/f5-renovate/containerbase", entrypoint)
        self.assertIn("/opt/f5-renovate/containerbase-runtime", entrypoint)
        self.assertIn("/opt/containerbase", entrypoint)
        self.assertIn("/tmp/containerbase", entrypoint)

    def test_rendered_runtime_seeds_writable_containerbase_state(self):
        rendered = self.render()
        main = rendered.split("containers:\n", 2)[-1].split("volumes:\n", 1)[0]
        self.assertIn("mountPath: /opt/containerbase", main)
        self.assertIn("name: containerbase", main)
        self.assertIn("sizeLimit: 2Gi", rendered)
        verifier = (ROOT / "scripts/verify-renovate-runtime.sh").read_text()
        self.assertIn("install-tool node 24.20.0", verifier)
        self.assertIn("Install tool node succeeded", verifier)

    def test_renovate_memory_budget_covers_memory_backed_working_sets(self):
        rendered = self.render()
        main = rendered.split("containers:\n", 2)[-1].split("volumes:\n", 1)[0]
        self.assertIn(
            "name: cache, emptyDir: {medium: Memory, sizeLimit: 2Gi}", rendered
        )
        self.assertIn(
            "name: tmp, emptyDir: {medium: Memory, sizeLimit: 4Gi}", rendered
        )
        self.assertIn(
            "name: containerbase, emptyDir: {medium: Memory, sizeLimit: 2Gi}",
            rendered,
        )
        self.assertRegex(
            main,
            r"resources:\s*\n\s+limits:\n\s+cpu: \"2\"\n\s+memory: 12Gi"
            r"\n\s+requests:\n\s+cpu: 250m\n\s+memory: 6Gi",
        )
        schema = json.loads((ROOT / "renovate-system/values.schema.json").read_text())
        self.assertEqual(
            {
                "requests": {"cpu": "250m", "memory": "6Gi"},
                "limits": {"cpu": "2", "memory": "12Gi"},
            },
            schema["properties"]["resources"]["properties"]["renovate"]["const"],
        )
        self.assertEqual(
            {"containerbaseSizeLimit": "2Gi"},
            schema["properties"]["ephemeralStorage"]["const"],
        )
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertIn('.resources.requests.memory == "6Gi"', deploy)
        self.assertIn('.resources.limits.memory == "12Gi"', deploy)
        self.assertIn('select(.name == "cache") |', deploy)
        self.assertIn('.emptyDir.sizeLimit == "2Gi"', deploy)
        self.assertIn('select(.name == "tmp") |', deploy)
        self.assertIn('.emptyDir.sizeLimit == "4Gi"', deploy)
        self.assertIn('select(.name == "containerbase") |', deploy)
        self.assertIn('.emptyDir.sizeLimit == "2Gi"', deploy)

    def test_release_contract_requires_active_production_schedule(self):
        schema = json.loads((ROOT / "renovate-system/values.schema.json").read_text())
        self.assertFalse(schema["properties"]["suspend"]["const"])
        values = (ROOT / "renovate-system/values.yaml").read_text()
        self.assertIn("suspend: false", values)
        self.assertIn('schedule: "20 5 * * *"', values)
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertIn(".spec.suspend == false", deploy)
        self.assertIn("deployed active Renovate CronJob", deploy)

    def test_rendered_runtime_uses_supported_config_and_upstream_non_root_identity(self):
        rendered = self.render()
        self.assertIn("runAsUser: 12021", rendered)
        self.assertIn("runAsGroup: 0", rendered)
        self.assertIn("name: RENOVATE_CONFIG_FILE", rendered)
        self.assertIn("value: /config/renovate.json", rendered)
        self.assertNotIn("--config-file=/config/renovate.json", rendered)


if __name__ == "__main__":
    unittest.main()
