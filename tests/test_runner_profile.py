from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "runner_profile", ROOT / "scripts/runner-profile.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RunnerProfileTests(unittest.TestCase):
    def make_cgroup(self, root: Path) -> None:
        (root / "cpu.stat").write_text(
            "usage_usec 100\nuser_usec 60\nsystem_usec 40\nnr_periods 2\nnr_throttled 1\nthrottled_usec 5\n",
            encoding="utf-8",
        )
        (root / "cpu.max").write_text("200000 100000\n", encoding="utf-8")
        (root / "memory.current").write_text("1024\n", encoding="utf-8")
        (root / "memory.peak").write_text("2048\n", encoding="utf-8")
        (root / "memory.max").write_text("4096\n", encoding="utf-8")
        (root / "memory.events").write_text(
            "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n", encoding="utf-8"
        )
        (root / "io.stat").write_text(
            "8:0 rbytes=10 wbytes=20 rios=1 wios=2 dbytes=0 dios=0\n"
            "8:1 rbytes=30 wbytes=40 rios=3 wios=4 dbytes=0 dios=0\n",
            encoding="utf-8",
        )

    def test_parsers_sum_io_and_accept_unlimited_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_cgroup(root)
            self.assertEqual(40, MODULE.parse_io(root / "io.stat")["rbytes"])
            self.assertEqual(2.0, MODULE.cpu_limit(root))
            (root / "memory.max").write_text("max\n", encoding="utf-8")
            self.assertIsNone(MODULE.read_int(root / "memory.max"))

    def test_exact_exit_code_schema_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_cgroup(root)
            output = root / "profile.json"
            env = {
                **os.environ,
                "GITHUB_REPOSITORY": "f5-sales-demo/example",
                "GITHUB_SHA": "a" * 40,
                "RUNNER_IMAGE_DIGEST": "sha256:" + "b" * 64,
                "SECRET_VALUE": "must-not-appear",
            }
            command = [
                sys.executable,
                str(ROOT / "scripts/runner-profile.py"),
                "--name",
                "unit",
                "--output",
                str(output),
                "--cgroup-root",
                str(root),
                "--",
                "sh",
                "-c",
                "exit 23",
                "must-not-appear-in-json",
            ]
            result = subprocess.run(command, env=env, check=False)
            self.assertEqual(23, result.returncode)
            raw = output.read_text(encoding="utf-8")
            self.assertNotIn("must-not-appear", raw)
            profile = json.loads(raw)
            self.assertEqual(23, profile["exit"]["code"])
            self.assertEqual("sha256:" + "b" * 64, profile["image_digest"])
            self.assertEqual(40, profile["io"]["rbytes"] - profile["io"]["rbytes"] + 40)
            schema = json.loads(
                (ROOT / "schemas/workload-profile.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(set(schema["required"]), set(profile))

    def test_signal_is_forwarded_and_profile_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_cgroup(root)
            output = root / "signal.json"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts/runner-profile.py"),
                    "--name",
                    "signal",
                    "--output",
                    str(output),
                    "--cgroup-root",
                    str(root),
                    "--",
                    "sh",
                    "-c",
                    "sleep 30",
                ]
            )
            time.sleep(0.2)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(-signal.SIGTERM, process.wait(timeout=5))
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(signal.SIGTERM, profile["exit"]["signal"])
            self.assertEqual(128 + signal.SIGTERM, profile["exit"]["code"])


if __name__ == "__main__":
    unittest.main()
