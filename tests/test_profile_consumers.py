"""Profiler CLI acceptance; refuse execution until unsafe paths are removed."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from retained_command import capture as capture

ROOT = Path(__file__).resolve().parents[1]


class ProfileConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in [
            "docker-action-profile.py",
            "runner-profile.py",
            "build-runner-image.py",
        ]:
            tree = ast.parse((ROOT / "scripts" / name).read_text())
            unsafe = [
                n.func.attr
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and (
                    n.func.attr in {"kill", "killpg", "terminate", "send_signal"}
                    or n.func.attr in {"run", "check_output", "check_call"}
                    and any(k.arg == "timeout" for k in n.keywords)
                )
            ]
            if unsafe:
                raise AssertionError(
                    f"Refusing unsafe runtime source: {name}: {unsafe}"
                )

    def directory(self):
        parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="profile-consumer-", dir=parent))

    def invoke(self, root, argv):
        folder = root / "cli"
        capture.launch(folder, argv, timeout=10)
        result = capture.wait(folder, budget=12)
        self.assertEqual("complete", result["status"], result)
        self.assertFalse(any(result["truncated"].values()), result)
        return result

    def runner(self, output, source, timeout=3):
        return [
            sys.executable,
            "-B",
            str(ROOT / "scripts/runner-profile.py"),
            "--name",
            "test",
            "--output",
            str(output),
            "--timeout",
            str(timeout),
            "--",
            sys.executable,
            "-B",
            "-c",
            source,
        ]

    def test_repeated_cli_preserves_output_and_executes_once(self):
        root = self.directory()
        count = root / "count"
        output = root / "profile.json"
        argv = self.runner(
            output,
            f"from pathlib import Path; Path({str(count)!r}).open('a').write('once\\n')",
        )
        first = self.invoke(root, argv)
        original = output.read_bytes()
        second = self.invoke(root / "second", argv)
        self.assertEqual(0, first["code"])
        self.assertEqual(0, second["code"])
        self.assertEqual("once\n", count.read_text())
        self.assertEqual(original, output.read_bytes())

    def test_timeout_keeps_partial_capture_and_cannot_resume_as_success(self):
        root = self.directory()
        output = root / "profile.json"
        argv = self.runner(
            output,
            "import time; print('partial',flush=True); time.sleep(2)",
            timeout=1,
        )
        first = self.invoke(root, argv)
        self.assertNotEqual(0, first["code"])
        observer = output.with_name(output.name + ".capture")
        request = json.loads((observer / "observation.private.json").read_bytes())
        outcome = capture.Observation.inspect(observer, request["config"])
        self.assertEqual("unresolved", outcome["result"])
        self.assertIn(
            b"partial", next(observer.glob("command-*/stdout.private.log")).read_bytes()
        )
        second = self.invoke(root / "second", argv)
        self.assertNotEqual(0, second["code"])
        self.assertEqual(
            outcome, capture.Observation.inspect(observer, request["config"])
        )

    def test_changed_resume_input_does_not_execute_or_overwrite(self):
        root = self.directory()
        output = root / "profile.json"
        self.assertEqual(0, self.invoke(root, self.runner(output, "pass"))["code"])
        original = output.read_bytes()
        changed = self.invoke(
            root / "changed", self.runner(output, "raise Exception('must not execute')")
        )
        self.assertNotEqual(0, changed["code"])
        self.assertEqual(original, output.read_bytes())

    def test_build_failure_retains_original_child_status(self):
        root = self.directory()
        spec = importlib.util.spec_from_file_location(
            "build", ROOT / "scripts/build-runner-image.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        records = []
        code = module.run_monitored(
            [sys.executable, "-B", "-c", "raise SystemExit(17)"],
            records.append,
            interval=0.01,
            sample=lambda: {},
            capture_root=root / "capture",
            timeout=3,
        )
        self.assertEqual(17, code)
        self.assertEqual(17, records[-1]["childReturnCode"])
        self.assertEqual(17, records[-1]["exitCode"])

    def test_installed_scripts_include_the_shared_module(self):
        self.assertIn(
            "scripts/profile_capture.py /usr/local/bin/profile_capture.py",
            (ROOT / "Dockerfile").read_text(),
        )

    def test_build_diagnostics_are_bound_to_the_original_output(self):
        root = self.directory()
        output = root / "diagnostics.jsonl"
        spec = importlib.util.spec_from_file_location(
            "build", ROOT / "scripts/build-runner-image.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with output.open("x") as stream:

            def emit(record):
                stream.write(json.dumps(record) + "\n")
                stream.flush()

            code = module.run_monitored(
                [sys.executable, "-B", "-c", "pass"],
                emit,
                interval=0.01,
                sample=lambda: {},
                capture_root=root / "capture",
                timeout=3,
                output=output,
            )
        self.assertEqual(0, code)
        request = json.loads((root / "capture/observation.private.json").read_bytes())
        output.write_text("foreign diagnostics")
        self.assertEqual(
            "observation_output_changed",
            capture.Observation.inspect(root / "capture", request["config"])["reason"],
        )


if __name__ == "__main__":
    unittest.main()
