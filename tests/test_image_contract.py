#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ImageContractTests(unittest.TestCase):
    def test_catalog_integrity_fields_are_immutable(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema_version"], 1)
        self.assertRegex(catalog["upstream_runner_images"]["revision"], r"^[0-9a-f]{40}$")
        self.assertGreaterEqual(len(catalog["setup_actions"]), 5)
        for tool in catalog["tools"]:
            self.assertTrue(tool["profiles"])
            self.assertTrue(tool["command"])
            if "sha256" in tool:
                self.assertRegex(tool["sha256"], r"^[0-9a-f]{64}$")

    def test_standard_target_has_no_docker_client(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        standard = dockerfile.split("FROM runner-base AS standard", 1)[1].split("FROM runner-base AS container-build", 1)[0]
        container_build = dockerfile.split("FROM runner-base AS container-build", 1)[1]
        self.assertNotIn("docker", standard.lower())
        self.assertIn("docker-buildx", container_build)
        self.assertIn("docker-compose", container_build)
        self.assertIn("USER runner", standard)
        self.assertIn("RUNNER_MANUALLY_TRAP_SIG=1", dockerfile)
        self.assertIn("ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT=1", dockerfile)
        self.assertIn("/home/runner /opt/actions-runner /runner-runtime", dockerfile)
        self.assertIn("tar --extract --gzip --file /tmp/actions-runner.tar.gz --directory /opt/actions-runner", dockerfile)
        self.assertIn("cp --archive --link /opt/actions-runner/. /home/runner/", dockerfile)
        self.assertIn("chown -R runner:runner /home/runner /opt/actions-runner", dockerfile)
        self.assertIn("find /home/runner -mindepth 1 -maxdepth 1", (ROOT / "scripts/runner-entrypoint.sh").read_text(encoding="utf-8"))
        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        self.assertIn('(Path("/home/runner"), Path("/opt/actions-runner"))', verifier)
        self.assertIn('Path("/opt/actions-runner").is_symlink()', verifier)
        self.assertNotIn("--output /tmp/gcloud.tar.gz /tmp/", dockerfile)
        self.assertIn("--output /tmp/uv.tar.gz", dockerfile)

    def test_only_github_hosted_workflows_build_or_publish(self) -> None:
        verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        publish = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: ubuntu-24.04", verify)
        self.assertIn("runs-on: ubuntu-24.04", publish)
        self.assertIn("--no-cache", verify)
        self.assertIn("--no-cache", publish)
        self.assertIn("--provenance=mode=max", publish)
        self.assertNotIn("--sbom=true", publish)
        self.assertIn("docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e", publish)
        self.assertIn("driver: docker-container", publish)
        self.assertIn("actions/attest-build-provenance@", publish)
        self.assertIn("docker buildx imagetools inspect --format", publish)
        self.assertNotIn("containerimage.digest", publish)
        self.assertNotIn("actions/attest-sbom@", publish)
        self.assertNotIn("SPDX SBOM", (ROOT / "scripts/verify-promotion.sh").read_text(encoding="utf-8"))
        self.assertNotRegex(verify, r"runs-on:\s*\[?self-hosted")
        self.assertNotRegex(publish, r"runs-on:\s*\[?self-hosted")

    def test_pr_image_validation_is_cancellable_without_affecting_publication(self) -> None:
        verify = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
        publish = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
        self.assertIn("self-hosted-runner-pr-image-validation", verify)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", verify)
        self.assertIn("publish-immutable-self-hosted-runner", publish)
        self.assertIn("cancel-in-progress: false", publish)

    def test_ruff_action_is_preloaded_in_the_immutable_tool_cache(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        ruff_action = catalog["setup_actions"]["astral-sh/ruff-action"]
        self.assertEqual(["0.16.0"], ruff_action["versions"])
        self.assertEqual(
            "/opt/hostedtoolcache/ruff/0.16.0/x86_64.complete",
            ruff_action["cache_paths"]["0.16.0"],
        )
        ruff = next(tool for tool in catalog["tools"] if tool["name"] == "ruff")
        self.assertEqual("0.16.0", ruff["version"])
        self.assertRegex(ruff["sha256"], r"^[0-9a-f]{64}$")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG RUFF_VERSION=0.16.0", dockerfile)
        self.assertIn('"$AGENT_TOOLSDIRECTORY/ruff/${RUFF_VERSION}/x86_64.complete"', dockerfile)

    def test_setup_uv_is_preloaded_in_the_immutable_tool_cache(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        setup_uv = catalog["setup_actions"]["astral-sh/setup-uv"]
        self.assertEqual(["0.8.24"], setup_uv["versions"])
        self.assertEqual(
            "/opt/hostedtoolcache/uv/0.8.24/x86_64.complete",
            setup_uv["cache_paths"]["0.8.24"],
        )
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64.complete"', dockerfile)
        self.assertIn('"$AGENT_TOOLSDIRECTORY/uv/${UV_VERSION}/x86_64/uv"', dockerfile)

    def test_setup_go_uses_the_action_compatible_lowercase_cache_path(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        setup_go = catalog["setup_actions"]["actions/setup-go"]
        self.assertEqual(
            "/opt/hostedtoolcache/go/1.25.12/x64.complete",
            setup_go["cache_path"],
        )
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('"$AGENT_TOOLSDIRECTORY/go/1.25.12/x64.complete"', dockerfile)
        self.assertNotIn('"$AGENT_TOOLSDIRECTORY/Go/1.25.12/x64.complete"', dockerfile)

    def test_setup_python_cache_has_python_and_pip_entrypoints(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        self.assertIn("ln -s python3 /opt/python-${PYTHON311_VERSION}/bin/python", dockerfile)
        self.assertIn("ln -s pip3 /opt/python-${PYTHON311_VERSION}/bin/pip", dockerfile)
        self.assertIn("ln -s python3 /opt/python-${PYTHON313_VERSION}/bin/python", dockerfile)
        self.assertIn("ln -s pip3 /opt/python-${PYTHON313_VERSION}/bin/pip", dockerfile)
        self.assertIn("cache PATH did not resolve its python and pip entrypoints", verifier)

    def test_pnpm_and_spectral_are_immutable_image_tools(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        tools = {tool["name"]: tool for tool in catalog["tools"]}
        self.assertEqual("11.3.0", tools["pnpm"]["version"])
        self.assertEqual("6.16.3", tools["spectral"]["version"])
        self.assertEqual(["11.3.0"], catalog["setup_actions"]["pnpm/action-setup"]["versions"])
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG PNPM_VERSION=11.3.0", dockerfile)
        self.assertIn("exec node /opt/pnpm/package/bin/pnpm.cjs", dockerfile)
        self.assertIn("npm ci --omit=dev --ignore-scripts --no-audit --no-fund", dockerfile)

    def test_xcsh_linux_test_dependencies_are_immutable_image_tools(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify-tools.py").read_text(encoding="utf-8")
        for package in (
            "clang", "libcairo2-dev", "libpango1.0-dev", "libjpeg-dev", "libgif-dev",
            "librsvg2-dev", "fd-find", "ripgrep", "imagemagick", "rustup",
            "gcc-aarch64-linux-gnu", "libc6-dev-arm64-cross", "xvfb", "xauth",
        ):
            self.assertIn(package, dockerfile)
        self.assertIn("aarch64-linux-gnu-gcc -x c - -o /tmp/runner-arm64-libc-smoke", verifier)
        self.assertIn("runner-{}-smoke", verifier)
        self.assertIn("ln -s /usr/bin/fdfind /usr/local/bin/fd", dockerfile)
        self.assertIn("ln -s /usr/bin/convert /usr/local/bin/magick", dockerfile)
        self.assertIn("USER runner", dockerfile)
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))
        rustup = next(tool for tool in catalog["tools"] if tool["name"] == "rustup")
        self.assertEqual("rustup --version", rustup["command"])
        clang = next(tool for tool in catalog["tools"] if tool["name"] == "clang")
        clangxx = next(tool for tool in catalog["tools"] if tool["name"] == "clang++")
        self.assertEqual("18.1.3", clang["version"])
        self.assertEqual("clang --version", clang["command"])
        self.assertEqual("clang++ --version", clangxx["command"])
        xvfb = next(tool for tool in catalog["tools"] if tool["name"] == "xvfb-run")
        self.assertEqual("xvfb-run --help", xvfb["command"])
        self.assertEqual(["standard", "container-build"], xvfb["profiles"])

if __name__ == "__main__":
    unittest.main()
