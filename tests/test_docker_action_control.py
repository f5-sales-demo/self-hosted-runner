from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "docker_action_profile_control", ROOT / "scripts/docker-action-profile.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DIGEST = "sha256:" + "a" * 64
ACTION = "1" * 40
MANIFEST = "sha256:" + "b" * 64


class DockerActionControlTests(unittest.TestCase):
    def fixture(self, root):
        output = root / "profile.json"
        config = {
            "kind": "docker-action",
            "arguments": {
                "phase": "super-linter",
                "image": "example.com/image@" + DIGEST,
                "output": str(output),
                "ready_file": str(root / "ready"),
                "expected_digest": DIGEST,
                "expected_action_commit": ACTION,
                "expected_manifest_digest": MANIFEST,
            },
            "cwd": str(ROOT),
        }
        observation = MODULE.Observation(
            output.with_name(output.name + ".capture"),
            config,
            4,
            sources=[ROOT / "scripts/docker-action-profile.py"],
        )
        return observation, output, config

    def args(self, operation, output):
        return [
            operation,
            "--output",
            str(output),
            "--name",
            "super-linter",
            "--image",
            "example.com/image@" + DIGEST,
            "--expected-digest",
            DIGEST,
            "--expected-action-commit",
            ACTION,
            "--expected-manifest-digest",
            MANIFEST,
            "--wait",
            "0",
        ]

    def control(self, operation, output, extra=None):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = MODULE.control_main(self.args(operation, output) + (extra or []))
        return code, json.loads(stdout.getvalue())

    def test_inspect_retains_original_clock_and_submits_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            before = (observation.root / "observation.private.json").read_bytes()
            code, result = self.control("inspect", output)
            self.assertEqual(0, code)
            self.assertEqual("pending", result["status"])
            self.assertFalse(result["submissionRepeated"])
            self.assertEqual(
                before, (observation.root / "observation.private.json").read_bytes()
            )
            self.assertEqual(
                [],
                json.loads((observation.root / "commands.private.json").read_text())[
                    "commands"
                ],
            )

    def test_readiness_requires_bound_owner_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, config = self.fixture(Path(directory))
            code, _ = self.control("wait-ready", output)
            self.assertNotEqual(0, code)
            observation.publish_marker(
                Path(config["arguments"]["ready_file"]), status="ready"
            )
            code, result = self.control("wait-ready", output)
            self.assertEqual(0, code)
            self.assertEqual("ready", result["status"])
            marker = Path(config["arguments"]["ready_file"])
            value = json.loads(marker.read_text())
            value["owner"]["startTicks"] = "foreign"
            marker.write_text(json.dumps(value))
            code, _ = self.control("wait-ready", output)
            self.assertNotEqual(0, code)

    def test_cancel_is_single_use_and_repeated_control_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            before = (observation.root / "observation.private.json").read_bytes()
            code, result = self.control("cancel", output)
            self.assertEqual(0, code)
            self.assertTrue(result["cancellationRequested"])
            marker = observation.root / "cancel.private.json"
            original = marker.read_bytes()
            self.assertTrue(observation.cancel_requested())
            code, result = self.control("cancel", output)
            self.assertEqual(0, code)
            self.assertTrue(result["cancellationRequested"])
            self.assertEqual(original, marker.read_bytes())
            self.assertEqual(
                before, (observation.root / "observation.private.json").read_bytes()
            )

    def test_foreign_expectations_and_cancel_marker_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            code, _ = self.control("cancel", output, ["--image", "example.com/foreign"])
            self.assertNotEqual(0, code)
            self.assertFalse((observation.root / "cancel.private.json").exists())
            (observation.root / "cancel.private.json").write_text('{"foreign":true}')
            code, _ = self.control("cancel", output)
            self.assertNotEqual(0, code)
            self.assertEqual(
                '{"foreign":true}',
                (observation.root / "cancel.private.json").read_text(),
            )

    def test_changed_request_and_stopped_owner_do_not_receive_control(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            with patch("profile_capture.process_state", return_value="T"):
                code, _ = self.control("cancel", output)
                self.assertNotEqual(0, code)
            self.assertFalse((observation.root / "cancel.private.json").exists())
            request = observation.root / "observation.private.json"
            value = json.loads(request.read_text())
            value["deadlineMonotonic"] += 1
            request.write_text(json.dumps(value))
            code, _ = self.control("cancel", output)
            self.assertNotEqual(0, code)
            self.assertFalse((observation.root / "cancel.private.json").exists())

    def test_complete_requires_original_owner_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            output.write_text("{}")
            self.assertTrue(observation.close())
            observation.finish(output, code=0, result="completed")
            code, _ = self.control("wait-complete", output)
            self.assertNotEqual(0, code)
            with patch.object(MODULE, "original_process_absent", return_value=True):
                code, result = self.control("wait-complete", output)
            self.assertEqual(0, code)
            self.assertEqual("complete", result["status"])
            output.write_text("changed")
            code, _ = self.control("wait-complete", output)
            self.assertNotEqual(0, code)

    def test_unresolved_terminal_never_becomes_complete_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            observation._incomplete = True
            output.write_text("{}")
            observation.close()
            observation.finish(output, code=0, result="completed")
            with patch.object(MODULE, "original_process_absent", return_value=True):
                code, result = self.control("wait-complete", output)
            self.assertNotEqual(0, code)
            self.assertEqual("unresolved", result["status"])

    def test_complete_rechecks_original_command_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            observation.run([sys.executable, "-c", "print('owned evidence')"])
            output.write_text("{}")
            observation.close()
            observation.finish(output, code=0, result="completed")
            owner = observation.binding["owner"]
            with patch.object(
                MODULE,
                "original_process_absent",
                side_effect=lambda original: original == owner,
            ):
                code, _ = self.control("wait-complete", output)
            self.assertNotEqual(0, code)

    def test_concurrent_cancel_race_accepts_only_same_bound_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            native = MODULE.Observation.request_cancel

            def concurrent(root, given):
                native(root, given)
                raise FileExistsError("another controller completed the same request")

            with patch.object(
                MODULE.Observation, "request_cancel", side_effect=concurrent
            ):
                code, result = self.control("cancel", output)
            self.assertEqual(0, code)
            self.assertTrue(result["cancellationRequested"])
            self.assertTrue(observation.cancel_requested())

    def test_malformed_config_returns_typed_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            observation, output, _ = self.fixture(Path(directory))
            request = observation.root / "observation.private.json"
            data = json.loads(request.read_text())
            data["config"] = []
            request.write_text(json.dumps(data))
            code, result = self.control("cancel", output)
            self.assertNotEqual(0, code)
            self.assertEqual("unresolved", result["status"])
            self.assertFalse((observation.root / "cancel.private.json").exists())

    def test_real_cli_waits_for_natural_exit_without_relaunch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "profile.json"
            config = {
                "kind": "docker-action",
                "cwd": str(ROOT),
                "arguments": dict(
                    zip(
                        (
                            "phase",
                            "image",
                            "output",
                            "ready_file",
                            "expected_digest",
                            "expected_action_commit",
                            "expected_manifest_digest",
                        ),
                        (
                            "super-linter",
                            "example.com/image@" + DIGEST,
                            str(output),
                            str(root / "ready"),
                            DIGEST,
                            ACTION,
                            MANIFEST,
                        ),
                    )
                ),
            }
            program = (
                "import sys,time,json; from pathlib import Path; "
                "from profile_capture import Observation; "
                "output=Path(sys.argv[1]); config=json.loads(sys.argv[2]); "
                "obs=Observation(output.with_name(output.name+'.capture'),config,4); "
                "obs.publish_marker(Path(config['arguments']['ready_file']),status='ready'); "
                "time.sleep(.2); output.write_text('{}'); obs.close(); "
                "obs.finish(output,code=0,result='completed')"
            )
            env = os.environ | {"PYTHONPATH": str(ROOT / "scripts")}
            child = subprocess.Popen(
                [sys.executable, "-c", program, str(output), json.dumps(config)],
                cwd=ROOT,
                env=env,
            )
            try:
                deadline = time.monotonic() + 3
                while not (root / "ready").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / "ready").exists())
                self.assertEqual(0, child.wait(timeout=3))
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts/docker-action-profile.py"),
                        "control",
                        *self.args("wait-complete", output),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("complete", json.loads(result.stdout)["status"])
                self.assertEqual(0, child.returncode)
            finally:
                child.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
