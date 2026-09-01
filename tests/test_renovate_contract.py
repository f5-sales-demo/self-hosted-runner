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
        self.assertIn("suspend: true", rendered)
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
        self.assertIn("USER 1001:1001", dockerfile)
        workflow = (ROOT / ".github/workflows/publish-renovate.yml").read_text()
        self.assertIn("--tag \"$image:$tag\" renovate-system", workflow)
        self.assertIn("attest-build-provenance", workflow)

    def test_promotion_and_deployment_reject_tags_and_non_acr_runtime(self):
        promote = ROOT / "scripts/promote-renovate-image.sh"
        for invalid in ("ghcr.io/f5-sales-demo/renovate:latest", f"docker.io/renovate/renovate@sha256:{ZERO}"):
            result = subprocess.run([promote, invalid, "a" * 40, "/tmp/unused"], cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(2, result.returncode)
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertIn("^f5salesdemoarcca\\.azurecr\\.io/renovate@", deploy)
        self.assertNotIn("renovate:latest", deploy)
        promotion = promote.read_text()
        self.assertIn("gh attestation verify", promotion)
        self.assertIn("--deny-self-hosted-runners", promotion)

    def test_socketless_prepull_accepts_only_acr_renovate_digest(self):
        schema = json.loads((ROOT / "arc/prepull/values.schema.json").read_text())
        pattern = re.compile(schema["properties"]["renovateImage"]["pattern"])
        self.assertIsNotNone(pattern.fullmatch(f"f5salesdemoarcca.azurecr.io/renovate@sha256:{ONE}"))
        self.assertIsNone(pattern.fullmatch(f"ghcr.io/f5-sales-demo/renovate@sha256:{ONE}"))
        self.assertIsNone(pattern.fullmatch("f5salesdemoarcca.azurecr.io/renovate:latest"))

    def test_repository_scoped_acr_pull_secret_fallback_is_strictly_wired(self):
        schema = json.loads((ROOT / "renovate-system/values.schema.json").read_text())
        self.assertIn("imagePullSecrets", schema["required"])
        secret_pattern = re.compile(schema["properties"]["imagePullSecrets"]["items"]["pattern"])
        self.assertIsNotNone(secret_pattern.fullmatch("renovate-acr-pull"))
        self.assertIsNone(secret_pattern.fullmatch("RenovatePull"))
        chart = (ROOT / "renovate-system/templates/cronjob.yaml").read_text()
        self.assertIn("imagePullSecrets:", chart)
        deploy = (ROOT / "scripts/renovate-deploy.sh").read_text()
        self.assertIn("RENOVATE_ACR_PULL_SECRET", deploy)
        self.assertIn("kubernetes.io/dockerconfigjson", deploy)
        self.assertIn("imagePullSecrets[0]=ghcr-pull", deploy)
        self.assertIn('imagePullSecrets[1]=$pull_secret', deploy)
        helper = ROOT / "scripts/renovate-acr-pull-secret.sh"
        source = helper.read_text()
        self.assertTrue(helper.stat().st_mode & 0o111)
        self.assertIn("repositories/renovate/content/read", source)
        self.assertNotIn("_repositories_pull", source)
        self.assertIn("--expiration-in-days", source)


if __name__ == "__main__":
    unittest.main()
