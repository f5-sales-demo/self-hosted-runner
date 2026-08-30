from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "docker_action_profile", ROOT / "scripts/docker-action-profile.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
DIGEST = "sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
CONTAINER_ID = "c" * 64


class DockerActionProfileTests(unittest.TestCase):
    def fake_docker(self, root: Path, mode: str = "complete") -> Path:
        path = root / "docker"
        source = f"""\
#!/usr/bin/env python3
import json
import sys
import time

args = sys.argv[1:]
mode = {mode!r}
digest = {DIGEST!r}
image_id = {IMAGE_ID!r}
container_id = {CONTAINER_ID!r}
if args[:2] == ["image", "inspect"]:
    print(json.dumps({{"Id": image_id, "RepoDigests": ["example@" + digest], "Size": 123456}}))
elif args[:1] == ["ps"]:
    print("d" * 64)
elif args[:1] == ["events"]:
    def event(action, target, **attributes):
        print(json.dumps({{"Action": action, "id": target, "Actor": {{"Attributes": attributes}}}}), flush=True)
    event("create", "d" * 64)
    event("create", container_id)
    if mode == "ambiguous":
        event("create", "e" * 64)
        time.sleep(30)
    elif mode == "cancel":
        time.sleep(30)
    else:
        time.sleep(0.15)
        event("oom", container_id)
        event("die", container_id, exitCode="23")
        time.sleep(30)
elif args[:2] == ["container", "inspect"]:
    print(json.dumps({{"Image": image_id, "State": {{"OOMKilled": False}}}}))
elif args[:1] == ["stats"]:
    if mode == "malformed":
        print(json.dumps({{"CPUPerc": "--", "PIDs": "--"}}))
    else:
        print(json.dumps({{
            "CPUPerc": "125.5%",
            "MemUsage": "128MiB / 2GiB",
            "MemPerc": "6.25%",
            "NetIO": "10MB / 20MB",
            "BlockIO": "3MB / 4MB",
            "PIDs": "37",
        }}))
elif args[:1] == ["wait"]:
    if mode in {{"ambiguous", "cancel"}}:
        time.sleep(30)
    print("23")
else:
    raise SystemExit(2)
"""
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        path.chmod(0o755)
        return path

    def command(self, docker: Path, output: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts/docker-action-profile.py"),
            "--name",
            "super-linter",
            "--image",
            "example@" + DIGEST,
            "--expected-digest",
            DIGEST,
            "--output",
            str(output),
            "--docker",
            str(docker),
            "--interval",
            "0.02",
            "--timeout",
            "3",
        ]

    def test_size_and_event_parsers(self) -> None:
        self.assertEqual(128 * 1024**2, MODULE.parse_size("128MiB"))
        self.assertEqual((10_000_000, 20_000_000), MODULE.parse_pair("10MB / 20MB"))
        self.assertEqual(1.255, MODULE.parse_percent("125.5%"))
        self.assertEqual(
            ("die", CONTAINER_ID, {"exitCode": "23"}),
            MODULE.event_fields(
                {
                    "Action": "die",
                    "id": CONTAINER_ID,
                    "Actor": {"Attributes": {"exitCode": "23"}},
                }
            ),
        )

    def test_completed_action_preserves_exit_and_redacts_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "profile.json"
            docker = self.fake_docker(root)
            env = {**os.environ, "SECRET_VALUE": "must-not-appear"}
            result = subprocess.run(self.command(docker, output), env=env, check=False)
            self.assertEqual(0, result.returncode)
            raw = output.read_text(encoding="utf-8")
            self.assertNotIn("must-not-appear", raw)
            profile = json.loads(raw)
            self.assertEqual("docker_action", profile["profile_kind"])
            self.assertEqual("completed", profile["observer"]["result"])
            self.assertEqual(23, profile["exit"]["code"])
            self.assertTrue(profile["memory"]["oom"])
            self.assertEqual(128 * 1024**2, profile["memory"]["peak_bytes"])
            self.assertEqual(37, profile["pids"]["peak"])
            schema = json.loads(
                (ROOT / "schemas/docker-action-profile.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(set(schema["required"]), set(profile))

    def test_transient_stats_and_unwritable_summary_do_not_abort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "profile.json"
            env = {
                **os.environ,
                "GITHUB_STEP_SUMMARY": str(root / "missing" / "summary.md"),
            }
            result = subprocess.run(
                self.command(self.fake_docker(root, "malformed"), output),
                env=env,
                check=False,
            )
            self.assertEqual(0, result.returncode)
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("completed", profile["observer"]["result"])
            self.assertEqual(0, profile["sample_count"])

    def test_ambiguity_fails_closed_with_valid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "ambiguous.json"
            result = subprocess.run(
                self.command(self.fake_docker(root, "ambiguous"), output), check=False
            )
            self.assertEqual(2, result.returncode)
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("ambiguous", profile["observer"]["result"])
            self.assertEqual(
                "multiple_matching_containers", profile["observer"]["detail"]
            )

    def test_signal_writes_cancelled_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "cancel.json"
            process = subprocess.Popen(
                self.command(self.fake_docker(root, "cancel"), output)
            )
            time.sleep(0.3)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(2, process.wait(timeout=5))
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("cancelled", profile["observer"]["result"])
            self.assertEqual("observer_signal", profile["observer"]["detail"])


if __name__ == "__main__":
    unittest.main()
