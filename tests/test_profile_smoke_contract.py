"""CI must retain the owned action until independent exit evidence is available."""

import subprocess
import tempfile
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

    def read_observer_cidfile(self, content):
        workflow = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
        steps = workflow["jobs"]["container-build"]["steps"]
        source = next(
            step["run"]
            for step in steps
            if step.get("name") == "Smoke-test selected Docker action profiling"
        )
        lines = source.splitlines()
        start = next(
            index
            for index, line in enumerate(lines)
            if "observer_cid" in line and '"$evidence/observer-container-id"' in line
        )
        end = next(
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("docker ps ")
        )
        block = "\n".join(lines[start:end])
        with tempfile.TemporaryDirectory() as directory:
            if content is not None:
                (Path(directory) / "observer-container-id").write_bytes(content)
            script = (
                "set -euo pipefail\nevidence=$1\n"
                + block
                + '\nprintf "%s\\n" "$observer_cid"\n'
            )
            return subprocess.run(
                ["bash", "-c", script, "cidfile-smoke", directory],
                capture_output=True,
            )

    def test_docker_cidfile_without_newline(self):
        container_id = b"a" * 64
        result = self.read_observer_cidfile(container_id)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(container_id + b"\n", result.stdout)

    def test_docker_cidfile_with_newline(self):
        container_id = b"b" * 64
        result = self.read_observer_cidfile(container_id + b"\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(container_id + b"\n", result.stdout)

    def test_invalid_or_missing_docker_cidfile_is_rejected(self):
        for content in [
            None,
            b"",
            b"a" * 63,
            b"g" * 64,
            b"a" * 64 + b"\nextra",
            b" a" * 32,
        ]:
            with self.subTest(content=content):
                result = self.read_observer_cidfile(content)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(b"", result.stdout)
