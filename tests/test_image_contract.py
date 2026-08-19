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
        self.assertIn("--sbom=true", publish)
        self.assertIn("docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f", publish)
        self.assertIn("driver: docker-container", publish)
        self.assertIn("actions/attest-build-provenance@", publish)
        self.assertNotIn("actions/attest-sbom@", publish)
        self.assertIn("BuildKit SPDX SBOM attestation", (ROOT / "scripts/verify-promotion.sh").read_text(encoding="utf-8"))
        self.assertNotRegex(verify, r"runs-on:\s*\[?self-hosted")
        self.assertNotRegex(publish, r"runs-on:\s*\[?self-hosted")

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

if __name__ == "__main__":
    unittest.main()
