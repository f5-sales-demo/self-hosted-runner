"""Prevent stale Docker image consumers and generated sidecar behavior."""

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def dind(path):
    values = yaml.safe_load((ROOT / path).read_text())
    return next(
        c for c in values["template"]["spec"]["initContainers"] if c["name"] == "dind"
    )


class DockerReleaseContractTests(unittest.TestCase):
    def test_all_docker_image_consumers_share_the_catalog_contract(self):
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        cli = catalog["base_images"]["docker_cli"]
        daemon = catalog["base_images"]["docker_dind"]
        for image in (cli, daemon):
            self.assertRegex(image, r"^docker\.io/library/docker@sha256:[0-9a-f]{64}$")
        self.assertIn("ARG DOCKER_CLI_IMAGE=" + cli, (ROOT / "Dockerfile").read_text())
        tool = next(t for t in catalog["tools"] if t["name"] == "docker-cli")
        self.assertEqual(tool["source"], cli)
        self.assertEqual(tool["version"], tool["expected"])
        for path in (
            "arc/container-build-values.yaml",
            "arc/gitops-container-build-values.yaml",
        ):
            self.assertEqual(dind(path)["image"], daemon)
        for path in ("scripts/arc-deploy.sh", "scripts/validate-arc.sh"):
            source = (ROOT / path).read_text()
            self.assertIn(".base_images.docker_dind", source)
            self.assertRegex(source, r"dind_image=\$\(jq -er ")

    def test_gitops_sidecar_retains_the_direct_lifecycle_and_metrics_contract(self):
        direct = dind("arc/container-build-values.yaml")
        gitops = dind("arc/gitops-container-build-values.yaml")
        self.assertEqual(gitops, direct)
        self.assertIsNone(re.search(r"\b(?:kill|trap)\s", gitops["args"][0]))

    def test_docker_release_updates_are_monitored_without_age_eligibility(self):
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        source = catalog["update_sources"]["docker-cli"]
        self.assertEqual(source["strategy"], "github-release")
        self.assertEqual(source["repository"], "moby/moby")
        self.assertNotIn("minimumReleaseAge", source)
