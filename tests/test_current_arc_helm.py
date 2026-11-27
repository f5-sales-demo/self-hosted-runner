"""Current ARC identities and real Helm 4 postrenderer qualification."""
import os
import json
import tempfile
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "ghcr.io/actions/gha-runner-scale-set-controller:0.15.1"
DIGEST = "sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90"
PINNED = "ghcr.io/actions/gha-runner-scale-set-controller@" + DIGEST

class CurrentArcHelmTest(unittest.TestCase):
    def render(self, manifest):
        return subprocess.run([str(ROOT / "scripts/arc-controller-post-renderer.py")], input=manifest, text=True, capture_output=True)

    def test_current_chart_and_cli_identities_are_coherent(self):
        for script in ("arc-deploy.sh", "validate-arc.sh"):
            source = (ROOT / "scripts" / script).read_text()
            self.assertIn("chart_version=0.15.1", source)
            self.assertIn("sha256:c2497a6dedaaa723322abfe9ea2f5a81b9b8fbb90b38e45d7835b6c7629a3aae", source)
            self.assertIn("sha256:2ae7e43db61047aa3c4182e73c8f58ace35e796fa07d41b64fb3c39e81e1f365", source)
            self.assertIn("v4.3.2+", source)
            self.assertIn("--post-renderer arc-controller-image", source)
            self.assertIn('HELM_PLUGINS="$repo_root/arc/helm-plugins"', source)
            self.assertNotIn("--post-renderer scripts/", source)
        self.assertIn('tag: "0.15.1"', (ROOT / "arc/controller-values.yaml").read_text())
        self.assertIn("v4.3.2+", (ROOT / "scripts/renovate-deploy.sh").read_text())
        workflow = (ROOT / ".github/workflows/terraform.yml").read_text()
        self.assertIn("helm-v4.3.2-linux-amd64.tar.gz", workflow)

    def test_postrenderer_rewrites_both_controller_identities(self):
        source = 'image: "' + SOURCE + '"\nvalue: "' + SOURCE + '"\n'
        result = self.render(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(PINNED), 2)
        self.assertNotIn(SOURCE, result.stdout)

    def test_missing_stale_and_ambiguous_identities_fail_without_output(self):
        for manifest in ("", "kind: Deployment\n", SOURCE + "\n", (SOURCE + "\n") * 3, (SOURCE.replace("0.15.1", "0.14.2") + "\n") * 2, (SOURCE + "-foreign\n") * 2):
            with self.subTest(manifest=manifest):
                result = self.render(manifest)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_helm4_plugin_has_no_install_hooks(self):
        plugin = ROOT / "arc/helm-plugins/arc-controller-image/plugin.yaml"
        self.assertTrue(plugin.is_file())
        source = plugin.read_text()
        self.assertIn("type: postrenderer/v1", source)
        self.assertIn("runtime: subprocess", source)
        self.assertIn("apiVersion: v1", source)
        self.assertIn("${HELM_PLUGIN_DIR}/../../../scripts/arc-controller-post-renderer.py", source)
        self.assertNotIn("hooks:", source)

    @unittest.skipUnless(os.environ.get("CURRENT_HELM") and os.environ.get("CURRENT_ARC_CONTROLLER_CHART"), "Requires qualified current executable and official chart")
    def test_real_helm4_executes_plugin_on_current_chart(self):
        result = subprocess.run([os.environ["CURRENT_HELM"], "template", "arc", os.environ["CURRENT_ARC_CONTROLLER_CHART"], "--namespace", "arc-systems", "--values", str(ROOT / "arc/controller-values.yaml"), "--post-renderer", "arc-controller-image"], env={**os.environ, "HELM_PLUGINS": str(ROOT / "arc/helm-plugins")}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(PINNED), 2)
        self.assertNotIn(SOURCE, result.stdout)
        for required in ("runner-profile: system", "runAsNonRoot: true", "readOnlyRootFilesystem: true", "memory: 512Mi"):
            self.assertIn(required, result.stdout)


class ArcRegistryContextTest(unittest.TestCase):
    def configure(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "source/config.json"
            original.parent.mkdir()
            original.write_text(raw)
            target = root / "temporary-registry.json"
            result = subprocess.run(["bash", str(ROOT / "scripts/arc-registry-context.sh"), str(target)], env={**os.environ, "GHCR_SOURCE_DOCKER_CONFIG": str(original.parent)}, capture_output=True, text=True)
            self.assertEqual(original.read_text(), raw)
            return result, json.loads(target.read_text()) if result.returncode == 0 else None, target.stat().st_mode & 0o777 if target.exists() else None

    def test_explicit_auth_retains_only_ghcr_credentials_privately(self):
        result, config, mode = self.configure(json.dumps({"auths": {"ghcr.io": {"auth": "synthetic-only"}, "foreign.example": {"auth": "foreign-only"}}, "headers": {"private": "excluded"}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config, {"auths": {"ghcr.io": {"auth": "synthetic-only"}}})
        self.assertEqual(mode, 0o600)
        self.assertNotIn("synthetic-only", result.stdout + result.stderr)
        self.assertNotIn("foreign-only", result.stdout + result.stderr)

    def test_global_helper_is_scoped_to_ghcr(self):
        result, config, _ = self.configure(json.dumps({"credsStore": "synthetic-helper", "auths": {"foreign.example": {"auth": "foreign-only"}}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config, {"auths": {}, "credHelpers": {"ghcr.io": "synthetic-helper"}})

    def test_invalid_or_multiple_documents_never_fall_back(self):
        for raw in ("malformed", "{} {}", "[]", json.dumps({"auths": {"foreign.example": {"auth": "foreign-only"}}})):
            with self.subTest(raw=raw):
                result, config, _ = self.configure(raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(config)
