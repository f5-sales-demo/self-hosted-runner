"""Current ARC identities and real Helm 4 postrenderer qualification."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "ghcr.io/actions/gha-runner-scale-set-controller:0.15.1"
DIGEST = "sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90"
PINNED = "ghcr.io/actions/gha-runner-scale-set-controller@" + DIGEST


class CurrentArcHelmTest(unittest.TestCase):
    def render(self, manifest):
        return subprocess.run(
            [str(ROOT / "scripts/arc-controller-post-renderer.py")],
            input=manifest,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_current_chart_and_cli_identities_are_coherent(self):
        for script in ("arc-deploy.sh", "validate-arc.sh"):
            source = (ROOT / "scripts" / script).read_text()
            self.assertIn("chart_version=0.15.1", source)
            self.assertIn(
                "sha256:c2497a6dedaaa723322abfe9ea2f5a81b9b8fbb90b38e45d7835b6c7629a3aae",
                source,
            )
            self.assertIn(
                "sha256:2ae7e43db61047aa3c4182e73c8f58ace35e796fa07d41b64fb3c39e81e1f365",
                source,
            )
            self.assertIn("v4.3.2+", source)
            self.assertIn("--post-renderer arc-controller-image", source)
            self.assertIn('HELM_PLUGINS="$repo_root/arc/helm-plugins"', source)
            self.assertNotIn("--post-renderer scripts/", source)
        self.assertIn(
            'tag: "0.15.1"', (ROOT / "arc/controller-values.yaml").read_text()
        )
        self.assertEqual(
            json.loads((ROOT / "renovate-system/image-source.json").read_text())[
                "runtime"
            ]["helm"],
            "4.3.2",
        )
        workflow = (ROOT / ".github/workflows/terraform.yml").read_text()
        self.assertIn("helm-v4.3.2-linux-amd64.tar.gz", workflow)

    def test_renovate_checks_current_source_bound_helm_before_deployment(self):
        prefix = (
            (ROOT / "scripts/renovate-deploy.sh")
            .read_text()
            .split("deploy_timeout=15m", 1)[0]
        )
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = folder / "kubeconfig"
            config.write_text("synthetic-only")
            config.chmod(0o600)
            (folder / "renovate-system").mkdir()
            manifest = folder / "renovate-system/image-source.json"
            (folder / "helm").write_text(
                '#!/bin/sh\nprintf "%s\\n" "$TEST_HELM_VERSION"\n'
            )
            (folder / "helm").chmod(0o700)
            (folder / "kubectl").write_text("#!/bin/sh\nexit 99\n")
            (folder / "kubectl").chmod(0o700)
            for declared, observed, success in (
                ("4.3.2", "v4.3.2+g8346aaa", True),
                ("4.3.2", "v4.3.2", True),
                ("4.3.2", "v4.3.1+g8346aaa", False),
                ("4.3.2", "v4.3.20", False),
                ("4.3.2; touch injected", "v4.3.2", False),
                (None, "v4.3.2", False),
            ):
                with self.subTest(declared=declared, observed=observed):
                    manifest.write_text(json.dumps({"runtime": {"helm": declared}}))
                    result = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            prefix,
                            "qualified-gate",
                            "synthetic-lock.json",
                        ],
                        cwd=folder,
                        env={
                            **os.environ,
                            "PATH": str(folder) + os.pathsep + os.environ["PATH"],
                            "KUBECONFIG": str(config),
                            "TEST_HELM_VERSION": observed,
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, success, result.stderr)
                    self.assertFalse((folder / "injected").exists())

    def test_runner_image_and_catalog_use_the_qualified_current_helm(self):
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        helm = next(tool for tool in catalog["tools"] if tool["name"] == "helm")
        self.assertEqual(helm["version"], "4.3.2")
        self.assertEqual(
            helm["source"], "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz"
        )
        self.assertEqual(
            helm["sha256"],
            "d1c0278d46beab79582d7e50f5b3dc206944ba8e577cde618513fd07daa53983",
        )
        self.assertEqual(helm["expected"], "v4.3.2")
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("ARG HELM_VERSION=4.3.2", dockerfile)
        self.assertIn("ARG HELM_SHA256=" + helm["sha256"], dockerfile)
        self.assertEqual(
            catalog["update_sources"]["helm"],
            {"strategy": "github-release", "repository": "helm/helm"},
        )

    def test_postrenderer_rewrites_both_controller_identities(self):
        source = 'image: "' + SOURCE + '"\nvalue: "' + SOURCE + '"\n'
        result = self.render(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(PINNED), 2)
        self.assertNotIn(SOURCE, result.stdout)

    def test_missing_stale_and_ambiguous_identities_fail_without_output(self):
        for manifest in (
            "",
            "kind: Deployment\n",
            SOURCE + "\n",
            (SOURCE + "\n") * 3,
            (SOURCE.replace("0.15.1", "0.14.2") + "\n") * 2,
            (SOURCE + "-foreign\n") * 2,
        ):
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
        self.assertIn(
            "${HELM_PLUGIN_DIR}/../../../scripts/arc-controller-post-renderer.py",
            source,
        )
        self.assertNotIn("hooks:", source)

    @unittest.skipUnless(
        os.environ.get("CURRENT_HELM")
        and os.environ.get("CURRENT_ARC_CONTROLLER_CHART"),
        "Requires qualified current executable and official chart",
    )
    def test_real_helm4_executes_plugin_on_current_chart(self):
        result = subprocess.run(
            [
                os.environ["CURRENT_HELM"],
                "template",
                "arc",
                os.environ["CURRENT_ARC_CONTROLLER_CHART"],
                "--namespace",
                "arc-systems",
                "--values",
                str(ROOT / "arc/controller-values.yaml"),
                "--post-renderer",
                "arc-controller-image",
            ],
            env={**os.environ, "HELM_PLUGINS": str(ROOT / "arc/helm-plugins")},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(PINNED), 2)
        self.assertNotIn(SOURCE, result.stdout)
        for required in (
            "runner-profile: system",
            "runAsNonRoot: true",
            "readOnlyRootFilesystem: true",
            "memory: 512Mi",
        ):
            self.assertIn(required, result.stdout)


class ArcRegistryContextTest(unittest.TestCase):
    def configure(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "source/config.json"
            original.parent.mkdir()
            original.write_text(raw)
            target = root / "temporary-registry.json"
            result = subprocess.run(
                ["bash", str(ROOT / "scripts/arc-registry-context.sh"), str(target)],
                env={**os.environ, "GHCR_SOURCE_DOCKER_CONFIG": str(original.parent)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(original.read_text(), raw)
            return (
                result,
                json.loads(target.read_text()) if result.returncode == 0 else None,
                target.stat().st_mode & 0o777 if target.exists() else None,
            )

    def test_explicit_auth_retains_only_ghcr_credentials_privately(self):
        result, config, mode = self.configure(
            json.dumps(
                {
                    "auths": {
                        "ghcr.io": {"auth": "synthetic-only"},
                        "foreign.example": {"auth": "foreign-only"},
                    },
                    "headers": {"private": "excluded"},
                }
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config, {"auths": {"ghcr.io": {"auth": "synthetic-only"}}})
        self.assertEqual(mode, 0o600)
        self.assertNotIn("synthetic-only", result.stdout + result.stderr)
        self.assertNotIn("foreign-only", result.stdout + result.stderr)

    def test_global_helper_is_scoped_to_ghcr(self):
        result, config, _ = self.configure(
            json.dumps(
                {
                    "credsStore": "synthetic-helper",
                    "auths": {"foreign.example": {"auth": "foreign-only"}},
                }
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            config, {"auths": {}, "credHelpers": {"ghcr.io": "synthetic-helper"}}
        )

    def test_invalid_or_multiple_documents_never_fall_back(self):
        for raw in (
            "malformed",
            "{} {}",
            "[]",
            json.dumps({"auths": {"foreign.example": {"auth": "foreign-only"}}}),
        ):
            with self.subTest(raw=raw):
                result, config, _ = self.configure(raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(config)
