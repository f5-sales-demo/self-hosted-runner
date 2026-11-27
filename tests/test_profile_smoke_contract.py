"""CI must retain the owned action until independent exit evidence is available."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ProfileSmokeContractTests(unittest.TestCase):
    def test_smoke_preserves_container_identity_and_capture_evidence(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
        steps = workflow["jobs"]["container-build"]["steps"]
        smoke = next(
            step
            for step in steps
            if step.get("name") == "Smoke-test selected Docker action profiling"
        )
        source = smoke["run"]
        self.assertNotIn('docker run --rm "$SMOKE_IMAGE"', source)
        self.assertNotIn('rm -rf "$evidence"', source)
        self.assertIn(".Created", source)
        self.assertIn(".Config.Labels", source)
        self.assertIn(".State.Running == false", source)
        self.assertNotIn("docker rm --force", source)
        uploads = [
            step
            for step in steps
            if step.get("if") == "always()"
            and "actions/upload-artifact@" in step.get("uses", "")
        ]
        self.assertTrue(
            any("docker-profile-smoke" in step["with"]["path"] for step in uploads)
        )
