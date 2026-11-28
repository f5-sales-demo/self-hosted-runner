from __future__ import annotations

import importlib.util
import io
import json
import os
import runpy
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

from retained_command import capture as CAPTURE

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


@contextmanager
def preserved_directory():
    parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    yield tempfile.mkdtemp(prefix="profiler-regression-", dir=parent)


class DockerActionProfileTests(unittest.TestCase):
    def fake_docker(self, root: Path, mode: str = "complete") -> Path:
        path = root / "docker"
        source = f"""\
#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
mode = {mode!r}
digest = {DIGEST!r}
image_id = {IMAGE_ID!r}
container_id = {CONTAINER_ID!r}
samples_path = Path(__file__ + ".stats-count")
exited_path = Path(__file__ + ".exited")
if args[:2] == ["image", "inspect"]:
    print(json.dumps({{"Id": image_id, "RepoDigests": ["example@" + digest], "Size": 123456}}))
elif args[:1] == ["ps"]:
    print("d" * 64)
elif args[:1] == ["events"]:
    def event(action, target, **attributes):
        print(json.dumps({{"Action": action, "id": target, "Actor": {{"Attributes": attributes}}}}), flush=True)
    if mode == "delayed":
        time.sleep(0.6)
    event("create", "d" * 64)
    event("create", container_id)
    if mode == "ambiguous":
        event("create", "e" * 64)
        time.sleep(max(0, float(args[args.index("--until") + 1]) - time.time()))
    elif mode == "cancel":
        time.sleep(max(0, float(args[args.index("--until") + 1]) - time.time()))
    else:
        # Keep the owned fixture running until the observer has captured two
        # samples, within the same finite event window. Startup speed cannot
        # silently turn a metrics test into an unsampled short action.
        until = float(args[args.index("--until") + 1])
        while mode != "fast" and time.time() < until:
            if samples_path.exists() and int(samples_path.read_text()) >= 2:
                break
            time.sleep(0.005)
        # A later contiguous window can observe completion after sampling.
        if mode == "fast" or (samples_path.exists() and int(samples_path.read_text()) >= 2):
            exited_path.write_text("exited")
            event("oom", container_id)
            event("die", container_id, exitCode="23")
        time.sleep(max(0, float(args[args.index("--until") + 1]) - time.time()))
elif args[:2] == ["container", "inspect"]:
    running = not exited_path.exists()
    print(json.dumps({{"Id": container_id, "Created": "2026-11-27T00:00:00Z", "Image": image_id, "State": {{"OOMKilled": False, "Running": running, "Status": "running" if running else "exited", "ExitCode": 23, "FinishedAt": "" if running else "2026-11-27T00:00:01Z"}}}}))
elif args[:1] == ["stats"]:
    count = int(samples_path.read_text()) if samples_path.exists() else 0
    temporary = samples_path.with_name(samples_path.name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(str(count + 1))
    temporary.replace(samples_path)
    if mode == "malformed":
        print(json.dumps({{"CPUPerc": "--", "PIDs": "--"}}))
    else:
        memory_limit = "2GiB"
        if mode == "zero_limit":
            memory_limit = "0B"
        elif mode == "flapping_limit":
            memory_limit = "2GiB" if count == 0 else "0B"
        print(json.dumps({{
            "CPUPerc": "125.5%",
            "MemUsage": "128MiB / " + memory_limit,
            "MemPerc": "6.25%",
            "NetIO": "10MB / 20MB",
            "BlockIO": "3MB / 4MB",
            "PIDs": "37",
        }}))
elif args[:1] == ["wait"]:
    if mode in {{"ambiguous", "cancel"}}:
        time.sleep(max(0, float(args[args.index("--until") + 1]) - time.time()))
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
            "10",
        ]

    def test_fixture_stats_counter_is_published_atomically(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            docker = self.fake_docker(root, "malformed")
            samples = Path(str(docker) + ".stats-count")
            samples.write_text("1")

            def interrupted_write(path, text, *args, **kwargs):
                with path.open("w", encoding="utf-8") as stream:
                    # Read exactly while the writer's destination is empty.
                    self.assertEqual(samples.read_text(), "1")
                    return stream.write(text)

            with (
                mock.patch.object(Path, "write_text", interrupted_write),
                mock.patch.object(sys, "argv", [str(docker), "stats"]),
                redirect_stdout(io.StringIO()),
            ):
                runpy.run_path(str(docker), run_name="__main__")
            self.assertEqual(samples.read_text(), "2")

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

    def test_seed_hit_requires_all_immutable_identities(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            result = root / "seed.json"
            seed_digest = "sha256:" + "d" * 64
            manifest_digest = "sha256:" + "e" * 64
            action_commit = "f" * 40
            result.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "result": "hit",
                        "qualified": True,
                        "reason": "verified_archive_loaded",
                        "action_commit": action_commit,
                        "index_digest": DIGEST,
                        "manifest_digest": manifest_digest,
                        "seed_image_digest": seed_digest,
                        "source_image_id": IMAGE_ID,
                        "loaded_image_id": "sha256:" + "a" * 64,
                        "image_config_sha256": "b" * 64,
                        "rootfs_sha256": "c" * 64,
                        "archive_sha256": "1" * 64,
                        "load_duration_seconds": 4,
                    }
                ),
                encoding="utf-8",
            )
            evidence = MODULE.seed_evidence(
                result,
                action_commit,
                DIGEST,
                manifest_digest,
                "example@" + seed_digest,
            )
            self.assertTrue(evidence["qualified"])
            self.assertEqual("hit", evidence["result"])
            mismatch = MODULE.seed_evidence(
                result,
                action_commit,
                DIGEST,
                "sha256:" + "2" * 64,
                "example@" + seed_digest,
            )
            self.assertFalse(mismatch["qualified"])
            self.assertEqual("mismatch", mismatch["result"])
            self.assertEqual("profile_identity_mismatch", mismatch["reason"])

            rejected_payload = json.loads(result.read_text(encoding="utf-8"))
            rejected_payload.update(
                result="rejected",
                qualified=False,
                reason="archive_load_failed",
                loaded_image_id="",
            )
            result.write_text(json.dumps(rejected_payload), encoding="utf-8")
            rejected = MODULE.seed_evidence(
                result,
                action_commit,
                DIGEST,
                manifest_digest,
                "example@" + seed_digest,
            )
            self.assertEqual("rejected", rejected["result"])
            self.assertIsNone(rejected["loaded_image_id"])

    def test_throttling_and_disk_evidence_are_bounded(self) -> None:
        evidence = MODULE.throttling_delta(
            {"periods": 10, "throttled_periods": 2, "throttled_nanoseconds": 100},
            {"periods": 30, "throttled_periods": 5, "throttled_nanoseconds": 400},
        )
        self.assertTrue(evidence["available"])
        self.assertEqual(20, evidence["periods"])
        self.assertEqual(3, evidence["throttled_periods"])
        self.assertEqual(0.15, evidence["ratio"])
        with preserved_directory() as directory:
            disk = MODULE.disk_evidence(Path(directory))
        self.assertTrue(disk["available"])
        self.assertLessEqual(disk["used_ratio"], 1)
        start = {
            "schema_version": 1,
            "cpu": {"nr_periods": 10, "nr_throttled": 2, "throttled_usec": 100},
            "memory": {
                "current_bytes": 100,
                "peak_bytes": 200,
                "limit_bytes": 1000,
                "oom_kill": 0,
            },
            "io": {"read_bytes": 1000, "write_bytes": 2000},
            "disk": {
                "capacity_bytes": 10000,
                "used_bytes": 3000,
                "available_bytes": 7000,
                "used_ratio": 0.3,
            },
        }
        end = {
            "schema_version": 1,
            "cpu": {"nr_periods": 30, "nr_throttled": 5, "throttled_usec": 400},
            "memory": {
                "current_bytes": 300,
                "peak_bytes": 500,
                "limit_bytes": 1000,
                "oom_kill": 0,
            },
            "io": {"read_bytes": 1500, "write_bytes": 2900},
            "disk": {
                "capacity_bytes": 10000,
                "used_bytes": 4000,
                "available_bytes": 6000,
                "used_ratio": 0.4,
            },
        }
        dind = MODULE.dind_delta(start, end, Path("/missing"))
        self.assertEqual(0.15, dind["cpu_throttling"]["ratio"])
        self.assertEqual(0.5, dind["memory"]["peak_limit_ratio"])
        self.assertEqual(500, dind["io"]["read_bytes"])
        self.assertEqual(900, dind["io"]["write_bytes"])
        self.assertEqual(0.4, dind["disk"]["used_ratio"])

    def test_completed_action_preserves_exit_and_redacts_inputs(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            output = root / "profile.json"
            docker = self.fake_docker(root)
            env = {**os.environ, "SECRET_VALUE": "must-not-appear"}
            result = subprocess.run(self.command(docker, output), env=env, check=False)
            self.assertEqual(0, result.returncode)
            raw = output.read_text(encoding="utf-8")
            self.assertNotIn("must-not-appear", raw)
            profile = json.loads(raw)
            self.assertEqual(2, profile["schema_version"])
            self.assertEqual("docker_action", profile["profile_kind"])
            self.assertEqual("unavailable", profile["seed"]["result"])
            self.assertFalse(profile["seed"]["qualified"])
            self.assertGreaterEqual(profile["timing"]["action_pull_seconds"], 0)
            self.assertIn("cpu_throttling", profile["dind"])
            self.assertIn("disk", profile["dind"])
            self.assertEqual("completed", profile["observer"]["result"])
            self.assertEqual(23, profile["exit"]["code"])
            self.assertTrue(profile["memory"]["oom"])
            self.assertGreaterEqual(profile["sample_count"], 2)
            self.assertEqual(128 * 1024**2, profile["memory"]["peak_bytes"])
            self.assertEqual(37, profile["pids"]["peak"])
            schema = json.loads(
                (ROOT / "schemas/docker-action-profile.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(set(schema["required"]), set(profile))

    def test_cpu_integration_excludes_wait_for_container_creation(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            output = root / "delayed.json"
            result = subprocess.run(
                self.command(self.fake_docker(root, "delayed"), output), check=False
            )
            self.assertEqual(0, result.returncode)
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertGreaterEqual(profile["sample_count"], 2)
            selected_duration = profile["timing"]["container_seconds"]
            self.assertGreaterEqual(
                profile["duration_seconds"] - selected_duration, 0.6
            )
            self.assertLessEqual(
                profile["cpu"]["usage_seconds"] / 1.255, selected_duration
            )

    def test_short_action_does_not_invent_resource_samples(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            output = root / "short.json"
            result = subprocess.run(
                self.command(self.fake_docker(root, "fast"), output), check=False
            )
            self.assertEqual(0, result.returncode)
            profile = json.loads(output.read_text())
            self.assertEqual(23, profile["exit"]["code"])
            self.assertEqual(0, profile["sample_count"])
            self.assertIsNone(profile["memory"]["peak_bytes"])
            self.assertIsNone(profile["pids"]["peak"])
            self.assertEqual(0, profile["cpu"]["usage_seconds"])

    def test_transient_stats_and_unwritable_summary_do_not_abort(self) -> None:
        with preserved_directory() as directory:
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

    def test_zero_memory_limit_is_normalized_to_null(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            output = root / "profile.json"
            result = subprocess.run(
                self.command(self.fake_docker(root, "zero_limit"), output), check=False
            )
            self.assertEqual(0, result.returncode)
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertIsNone(profile["memory"]["limit_bytes"])
            self.assertIsNone(profile["memory"]["peak_limit_ratio"])

    def test_zero_sample_does_not_erase_a_positive_memory_limit(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            output = root / "profile.json"
            result = subprocess.run(
                self.command(self.fake_docker(root, "flapping_limit"), output),
                check=False,
            )
            self.assertEqual(0, result.returncode)
            profile = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(2 * 1024**3, profile["memory"]["limit_bytes"])
            self.assertEqual(0.0625, profile["memory"]["peak_limit_ratio"])

    def test_ambiguity_fails_closed_with_valid_evidence(self) -> None:
        with preserved_directory() as directory:
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

    def test_bound_control_writes_cancelled_profile(self) -> None:
        with preserved_directory() as directory:
            root = Path(directory)
            output = root / "cancel.json"
            ready = root / "ready.json"
            process = subprocess.Popen(
                self.command(self.fake_docker(root, "cancel"), output)
                + ["--ready-file", str(ready)]
            )
            deadline = time.monotonic() + 2
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            marker = json.loads(ready.read_bytes())
            observer = Path(marker["capture"])
            request = json.loads((observer / "observation.private.json").read_bytes())
            CAPTURE.Observation.request_cancel(observer, request["config"])
            self.assertEqual(2, process.wait(timeout=5))
            profile = json.loads(output.read_text())
            self.assertEqual("cancelled", profile["observer"]["result"])
            self.assertEqual("observer_cancel_requested", profile["observer"]["detail"])


if __name__ == "__main__":
    unittest.main()
