"""Qualify pilot scheduling, reviewed image binding and sidecar lifecycle."""

import re
import subprocess
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class ArcPilotTests(unittest.TestCase):
    def workflow(self):
        return yaml.safe_load((ROOT / ".github/workflows/arc-pilot.yml").read_text())

    def test_all_profiles_run_sequentially_without_canceling_an_existing_pilot(self):
        workflow = self.workflow()
        jobs = workflow["jobs"]
        expected = [
            "socketless",
            "compute",
            "container-build",
            "container-build-cleanroom",
        ]
        self.assertEqual(set(jobs), set(expected))
        for previous, current in pairwise(expected):
            self.assertEqual(jobs[current]["needs"], previous)
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])

    def test_each_profile_requires_its_reviewed_image_without_a_default(self):
        workflow = self.workflow()
        trigger = workflow.get("on", workflow.get(True))
        inputs = trigger["workflow_dispatch"]["inputs"]
        for profile in ("socketless", "compute", "container-build"):
            name = (
                "container-build-image"
                if profile == "container-build"
                else "standard-image"
            )
            self.assertTrue(inputs[name]["required"])
            self.assertNotIn("default", inputs[name])
            step = next(
                s
                for s in workflow["jobs"][profile]["steps"]
                if s.get("name", "").startswith("Verify")
            )
            self.assertEqual(
                step["env"]["EXPECTED_RUNNER_IMAGE"], "${{ inputs." + name + " }}"
            )

    def test_wrong_image_fails_before_catalog_execution_for_each_profile(self):
        workflow = self.workflow()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            calls = folder / "calls"
            tool = folder / "verify-runner-tools"
            tool.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$PILOT_CALLS"\n')
            tool.chmod(0o700)
            for profile in ("socketless", "compute", "container-build"):
                with self.subTest(profile=profile):
                    step = next(
                        s
                        for s in workflow["jobs"][profile]["steps"]
                        if s.get("name", "").startswith("Verify")
                    )
                    result = subprocess.run(
                        ["/bin/bash", "-c", step["run"]],
                        env={
                            "PATH": str(folder),
                            "PILOT_CALLS": str(calls),
                            "RUNNER_PROFILE": profile,
                            "EXPECTED_RUNNER_IMAGE": "ghcr.io/f5-sales-demo/self-hosted-runner@sha256:"
                            + "a" * 64,
                            "RUNNER_IMAGE_DIGEST": "ghcr.io/f5-sales-demo/self-hosted-runner@sha256:"
                            + "b" * 64,
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(calls.exists())
                    calls.unlink(missing_ok=True)

    def test_dind_uses_daemon_pid_one_and_no_explicit_process_signals(self):
        values = yaml.safe_load((ROOT / "arc/container-build-values.yaml").read_text())
        sidecar = next(
            c
            for c in values["template"]["spec"]["initContainers"]
            if c["name"] == "dind"
        )
        script = sidecar["args"][0]
        self.assertIsNone(re.search(r"\b(?:kill|trap)\s", script))
        self.assertEqual(
            script.rstrip().splitlines()[-1],
            "exec dockerd --host=unix:///var/run/docker.sock --group=123",
        )
        self.assertEqual(sidecar["restartPolicy"], "Always")
        self.assertEqual(
            sidecar["resources"]["requests"], {"cpu": "9", "memory": "24Gi"}
        )
        self.assertEqual(
            sidecar["resources"]["limits"], {"cpu": "10", "memory": "32Gi"}
        )


if __name__ == "__main__":
    unittest.main()
