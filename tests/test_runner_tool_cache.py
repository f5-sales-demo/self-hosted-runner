from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INITIALIZER = ROOT / "scripts" / "prepare-runner-tool-cache.sh"


class RunnerToolCacheTests(unittest.TestCase):
    def test_initializes_private_cache_inside_runtime_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            image_cache = root / "image-cache"
            marker = image_cache / "Python" / "3.13.7" / "x64.complete"
            runtime.mkdir()
            marker.parent.mkdir(parents=True)
            marker.touch()
            result = subprocess.run(
                [str(INITIALIZER)],
                text=True,
                capture_output=True,
                check=False,
                env={
                    "PATH": os.environ["PATH"],
                    "RUNNER_RUNTIME_DIR": str(runtime),
                    "RUNNER_TOOL_CACHE": str(runtime / "_tool"),
                    "AGENT_TOOLSDIRECTORY": str(image_cache),
                },
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((runtime / "_tool" / "Python" / "3.13.7" / "x64.complete").is_file())
            self.assertEqual("0o700", oct((runtime / "_tool").stat().st_mode & 0o777))

    def test_rejects_cache_outside_runtime_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            image_cache = root / "image-cache"
            runtime.mkdir()
            image_cache.mkdir()
            result = subprocess.run(
                [str(INITIALIZER)],
                text=True,
                capture_output=True,
                check=False,
                env={
                    "PATH": os.environ["PATH"],
                    "RUNNER_RUNTIME_DIR": str(runtime),
                    "RUNNER_TOOL_CACHE": str(root / "outside"),
                    "AGENT_TOOLSDIRECTORY": str(image_cache),
                },
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("inside the runtime workspace", result.stderr)


if __name__ == "__main__":
    unittest.main()
