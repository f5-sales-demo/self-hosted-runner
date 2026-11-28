"""Verify retained test invocation evidence and unchanged parent environment."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from retained_command import capture, run_command


class RetainedCommandTests(unittest.TestCase):
    def root(self):
        parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="retained-command-", dir=parent))

    def test_environment_and_exit_are_retained_without_parent_mutation(self):
        folder = self.root() / "capture"
        before = dict(os.environ)
        argv = [
            sys.executable,
            "-B",
            "-c",
            "import os,sys; print(os.environ['FIXTURE']); sys.exit(23)",
        ]
        result = run_command(
            argv, folder, 3, env={**before, "FIXTURE": "exact value"}, text=True
        )
        self.assertEqual(23, result.returncode)
        self.assertEqual("exact value\n", result.stdout)
        self.assertEqual(before, dict(os.environ))
        original = capture.resume(folder)
        self.assertEqual(original, capture.resume(folder))
        self.assertFalse(original["submissionRepeated"])
        with self.assertRaises(FileExistsError):
            run_command(argv, folder, 3)

    def test_deadline_failure_retains_partial_capture_and_natural_closure(self):
        folder = self.root() / "capture"
        argv = [
            sys.executable,
            "-B",
            "-c",
            "import time; print('partial',flush=True); time.sleep(.8)",
        ]
        with self.assertRaises(capture.ObservationIncomplete):
            run_command(argv, folder, 0.5)
        self.assertIn(b"partial", (folder / "stdout.private.log").read_bytes())
        result = capture.wait(folder, budget=3)
        self.assertEqual("complete", result["status"])
        self.assertFalse(result["withinDeadline"])
        self.assertFalse(result["submissionRepeated"])
