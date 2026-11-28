from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class CurrentSourceSbomExportTests(unittest.TestCase):
    def workflow(self):
        return yaml.safe_load(
            (ROOT / ".github/workflows/verify-sbom-export.yml").read_text()
        )

    def export_step(self):
        return next(
            step
            for step in self.workflow()["jobs"]["sbom-export"]["steps"]
            if step.get("name", "").startswith("Export and verify")
        )

    def test_checkout_and_export_bind_exact_pr_head(self):
        workflow = self.workflow()
        checkout = workflow["jobs"]["sbom-export"]["steps"][0]
        self.assertEqual(
            checkout["with"].get("ref"), "${{ github.event.pull_request.head.sha }}"
        )
        self.assertEqual(
            self.export_step()["env"].get("SOURCE_SHA"),
            "${{ github.event.pull_request.head.sha }}",
        )
        self.assertNotIn("BASE_SOURCE_SHA", self.export_step()["run"])

    def test_all_profiles_export_their_current_target_and_keep_release_separate(self):
        workflow = self.workflow()
        self.assertEqual(
            workflow["jobs"]["sbom-export"]["strategy"]["matrix"]["include"],
            [
                {"profile": "standard", "target": "standard"},
                {"profile": "container-build", "target": "container-build"},
                {"profile": "super-linter-seed", "target": "super-linter-seed"},
            ],
        )
        command = self.export_step()["run"]
        self.assertIn('--target "$TARGET"', command)
        self.assertIn('--tag "$candidate_reference"', command)
        self.assertIn("--no-cache --platform linux/amd64", command)
        self.assertIn("promotionEligible=false", command)
        self.assertIn("--attribution-report", command)
        self.assertIn('--expected-source-commit "$SOURCE_SHA"', command)
        self.assertNotIn("FROM %s", command)
        self.assertNotIn("gh attestation verify", command)
        self.assertNotIn("--push", command)

    def test_wrong_checkout_refuses_before_any_image_command(self):
        prefix = self.export_step()["run"].split('scanner="', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            git = folder / "git"
            git.write_text('#!/bin/sh\nprintf "%s\\n" "$OBSERVED_SOURCE"\n')
            git.chmod(0o700)
            result = subprocess.run(
                ["/bin/bash", "-c", prefix],
                env={
                    **os.environ,
                    "PATH": str(folder),
                    "SOURCE_SHA": "a" * 40,
                    "OBSERVED_SOURCE": "b" * 40,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(
                result.returncode, 0, "Foreign checkout must not enter the exporter"
            )
