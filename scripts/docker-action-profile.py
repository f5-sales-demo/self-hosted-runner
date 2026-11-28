#!/usr/bin/env python3
"""Observe one newly-created Docker action container without recording its inputs."""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import signal
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from profile_capture import (
    Observation,
    ObservationIncomplete,
    original_process_absent,
    resume,
)

SCHEMA_VERSION = 2
SUPER_LINTER_ACTION_COMMIT = "2da136927bd4a73596db63044b504547c62cb854"
SUPER_LINTER_INDEX_DIGEST = (
    "sha256:7620fb6f07a1908b0642d9ddc4f49da7d9f2b9497bcafee4d3d151d1e5567c0b"
)
SUPER_LINTER_AMD64_MANIFEST = (
    "sha256:496fe2e10c487771d42eede90cacfc767324db5ac9ddc9f3b2f490afa0a1db73"
)
SAFE_ENV = {
    "repository": "GITHUB_REPOSITORY",
    "commit": "GITHUB_SHA",
    "run_id": "GITHUB_RUN_ID",
    "run_attempt": "GITHUB_RUN_ATTEMPT",
    "job_id": "GITHUB_JOB",
    "runner_name": "RUNNER_NAME",
    "runner_profile": "RUNNER_PROFILE",
    "runner_image_digest": "RUNNER_IMAGE_DIGEST",
}
SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*", value)
    if not match:
        raise ValueError("invalid Docker size")
    unit = match.group(2).upper()
    if unit not in SIZE_UNITS:
        raise ValueError("unsupported Docker size unit")
    return round(float(match.group(1)) * SIZE_UNITS[unit])


def parse_pair(value: str) -> tuple[int, int]:
    fields = value.split("/")
    if len(fields) != 2:
        raise ValueError("invalid Docker counter pair")
    return parse_size(fields[0]), parse_size(fields[1])


def parse_percent(value: str) -> float:
    if not value.endswith("%"):
        raise ValueError("invalid Docker percentage")
    return max(0.0, float(value[:-1]) / 100.0)


def run_json(
    docker: str, arguments: list[str], observation: Observation, *, allow_failure=False
) -> dict:
    payload = json.loads(
        observation.run([docker, *arguments], allow_failure=allow_failure)
    )
    if not isinstance(payload, dict):
        raise TypeError("Docker returned a non-object")
    return payload


def image_identity(
    docker: str, reference: str, expected: str | None, observation: Observation
) -> dict:
    image = run_json(
        docker,
        ["image", "inspect", "--format", "{{json .}}", reference],
        observation,
        allow_failure=True,
    )
    image_id = image.get("Id")
    repo_digests = sorted(
        value.rsplit("@", 1)[1]
        for value in image.get("RepoDigests", [])
        if isinstance(value, str)
        and "@" in value
        and SHA256.fullmatch(value.rsplit("@", 1)[1])
    )
    if not isinstance(image_id, str) or not SHA256.fullmatch(image_id):
        raise ValueError("selected image has no immutable image ID")
    if not repo_digests:
        raise ValueError("selected image has no immutable repository digest")
    if expected and expected not in {image_id, *repo_digests}:
        raise ValueError("selected image does not match the expected digest")
    size = image.get("Size")
    if not isinstance(size, int) or size < 0:
        raise ValueError("selected image has invalid size metadata")
    return {"id": image_id, "digest": expected or repo_digests[0], "size_bytes": size}


def seed_evidence(
    path: Path | None,
    expected_action: str,
    expected_index: str,
    expected_manifest: str,
    expected_seed_image: str | None,
) -> dict:
    unavailable = {
        "result": "unavailable",
        "qualified": False,
        "reason": "seed_result_unavailable",
        "action_commit": None,
        "index_digest": None,
        "manifest_digest": None,
        "seed_image_digest": None,
        "source_image_id": None,
        "loaded_image_id": None,
        "image_config_sha256": None,
        "rootfs_sha256": None,
        "archive_sha256": None,
        "load_duration_seconds": None,
    }
    if path is None or not path.is_file():
        return unavailable
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**unavailable, "result": "rejected", "reason": "invalid_seed_result"}
    required = {
        "schema_version",
        "result",
        "qualified",
        "reason",
        "action_commit",
        "index_digest",
        "manifest_digest",
        "seed_image_digest",
        "source_image_id",
        "loaded_image_id",
        "image_config_sha256",
        "rootfs_sha256",
        "archive_sha256",
        "load_duration_seconds",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema_version") != 2
    ):
        return {**unavailable, "result": "rejected", "reason": "invalid_seed_result"}
    seed_digest = (
        expected_seed_image.rsplit("@", 1)[1]
        if expected_seed_image and "@" in expected_seed_image
        else None
    )
    identity_matches = (
        value.get("action_commit") == expected_action
        and value.get("index_digest") == expected_index
        and value.get("manifest_digest") == expected_manifest
        and seed_digest is not None
        and SHA256.fullmatch(seed_digest) is not None
        and value.get("seed_image_digest") == seed_digest
        and SHA256.fullmatch(str(value.get("source_image_id"))) is not None
        and SHA256.fullmatch(str(value.get("loaded_image_id"))) is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("image_config_sha256")))
        is not None
        and re.fullmatch(r"[0-9a-f]{64}", str(value.get("rootfs_sha256"))) is not None
    )
    source_hit = value.get("result") == "hit" and value.get("qualified") is True
    result = value.get("result")
    if result not in {"hit", "fallback", "rejected"}:
        result = "rejected"
    qualified = source_hit and identity_matches
    if source_hit and not identity_matches:
        result = "mismatch"
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason:
        reason = "invalid_seed_result"
        result = "rejected"
        qualified = False
    duration = value.get("load_duration_seconds")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
    ):
        duration = None
        qualified = False
        result = "rejected"
    evidence = {
        key: value.get(key)
        for key in unavailable
        if key not in {"result", "qualified", "reason"}
    }
    for field in ("source_image_id", "loaded_image_id"):
        if SHA256.fullmatch(str(evidence[field])) is None:
            evidence[field] = None
    for field in ("image_config_sha256", "rootfs_sha256", "archive_sha256"):
        if re.fullmatch(r"[0-9a-f]{64}", str(evidence[field])) is None:
            evidence[field] = None
    return {
        "result": result,
        "qualified": qualified,
        "reason": reason
        if qualified or result != "mismatch"
        else "profile_identity_mismatch",
        **evidence,
        "load_duration_seconds": duration,
    }


def throttling_delta(start: dict | None, end: dict | None) -> dict:
    unavailable = {
        "available": False,
        "periods": None,
        "throttled_periods": None,
        "throttled_seconds": None,
        "ratio": None,
    }
    if start is None or end is None:
        return unavailable
    periods = end["periods"] - start["periods"]
    throttled = end["throttled_periods"] - start["throttled_periods"]
    nanoseconds = end["throttled_nanoseconds"] - start["throttled_nanoseconds"]
    if min(periods, throttled, nanoseconds) < 0:
        return unavailable
    return {
        "available": True,
        "periods": periods,
        "throttled_periods": throttled,
        "throttled_seconds": nanoseconds / 1_000_000_000,
        "ratio": throttled / periods if periods else 0.0,
    }


def read_dind_evidence(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        return None
    return value


def dind_delta(start: dict | None, end: dict | None, storage: Path) -> dict:
    unavailable_memory = {
        "available": False,
        "current_bytes": None,
        "peak_bytes": None,
        "limit_bytes": None,
        "peak_limit_ratio": None,
        "oom_kill": None,
    }
    unavailable_io = {"available": False, "read_bytes": None, "write_bytes": None}
    if not isinstance(start, dict) or not isinstance(end, dict):
        return {
            "cpu_throttling": throttling_delta(None, None),
            "memory": unavailable_memory,
            "io": unavailable_io,
            "disk": disk_evidence(storage),
        }
    start_cpu = start.get("cpu")
    end_cpu = end.get("cpu")
    cpu = None
    if isinstance(start_cpu, dict) and isinstance(end_cpu, dict):
        try:
            cpu = {
                "periods": int(start_cpu["nr_periods"]),
                "throttled_periods": int(start_cpu["nr_throttled"]),
                "throttled_nanoseconds": int(start_cpu["throttled_usec"]) * 1000,
            }
            cpu_end = {
                "periods": int(end_cpu["nr_periods"]),
                "throttled_periods": int(end_cpu["nr_throttled"]),
                "throttled_nanoseconds": int(end_cpu["throttled_usec"]) * 1000,
            }
        except (KeyError, TypeError, ValueError):
            cpu = cpu_end = None
    else:
        cpu_end = None
    memory = unavailable_memory
    end_memory = end.get("memory")
    start_memory = start.get("memory")
    if isinstance(end_memory, dict) and isinstance(start_memory, dict):
        try:
            current = int(end_memory["current_bytes"])
            peak = int(end_memory["peak_bytes"])
            limit_value = int(end_memory["limit_bytes"])
            limit = limit_value if limit_value > 0 else None
            oom_kill = max(
                0, int(end_memory["oom_kill"]) - int(start_memory["oom_kill"])
            )
            if min(current, peak) < 0:
                raise ValueError
            memory = {
                "available": True,
                "current_bytes": current,
                "peak_bytes": peak,
                "limit_bytes": limit,
                "peak_limit_ratio": peak / limit if limit else None,
                "oom_kill": oom_kill,
            }
        except (KeyError, TypeError, ValueError):
            memory = unavailable_memory
    io = unavailable_io
    start_io = start.get("io")
    end_io = end.get("io")
    if isinstance(start_io, dict) and isinstance(end_io, dict):
        try:
            read_bytes = int(end_io["read_bytes"]) - int(start_io["read_bytes"])
            write_bytes = int(end_io["write_bytes"]) - int(start_io["write_bytes"])
            if min(read_bytes, write_bytes) < 0:
                raise ValueError
            io = {
                "available": True,
                "read_bytes": read_bytes,
                "write_bytes": write_bytes,
            }
        except (KeyError, TypeError, ValueError):
            io = unavailable_io
    disk = end.get("disk")
    required_disk = {"capacity_bytes", "used_bytes", "available_bytes", "used_ratio"}
    if not isinstance(disk, dict) or set(disk) != required_disk:
        disk = disk_evidence(storage)
    else:
        disk = {"available": True, **disk}
    return {
        "cpu_throttling": throttling_delta(cpu, cpu_end),
        "memory": memory,
        "io": io,
        "disk": disk,
    }


def disk_evidence(path: Path) -> dict:
    unavailable = {
        "available": False,
        "capacity_bytes": None,
        "used_bytes": None,
        "available_bytes": None,
        "used_ratio": None,
    }
    try:
        status = os.statvfs(path)
    except OSError:
        return unavailable
    capacity = status.f_blocks * status.f_frsize
    available = status.f_bavail * status.f_frsize
    used = max(0, capacity - status.f_bfree * status.f_frsize)
    return {
        "available": True,
        "capacity_bytes": capacity,
        "used_bytes": used,
        "available_bytes": available,
        "used_ratio": used / capacity if capacity else 0.0,
    }


def existing_containers(
    docker: str, image_id: str, observation: Observation
) -> set[str]:
    result = observation.run(
        [
            docker,
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"ancestor={image_id}",
            "--format",
            "{{.ID}}",
        ],
    )
    return {line.strip() for line in result.decode().splitlines() if line.strip()}


def event_fields(payload: dict) -> tuple[str, str, dict]:
    action = payload.get("Action") or payload.get("status") or ""
    container_id = payload.get("id") or payload.get("ID") or ""
    actor = payload.get("Actor") if isinstance(payload.get("Actor"), dict) else {}
    container_id = container_id or actor.get("ID") or ""
    attributes = (
        actor.get("Attributes") if isinstance(actor.get("Attributes"), dict) else {}
    )
    return str(action), str(container_id), attributes


class EventReader:
    """Finite, contiguous event windows; retain the original command and partial log."""

    def __init__(self, docker: str, image_reference: str, observation: Observation):
        self.events: queue.Queue[dict] = queue.Queue(maxsize=4096)
        self.docker, self.image_reference, self.observation = (
            docker,
            image_reference,
            observation,
        )
        self.since = observation.request["startedEpoch"]
        self.until = self.since
        self.folder: Path | None = None
        self.offset = 0
        self.partial = b""

    def start(self) -> None:
        remaining = self.observation.deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationIncomplete("original_event_deadline_expired")
        self.until = min(
            time.time() + 2,
            self.observation.request["startedEpoch"]
            + self.observation.deadline
            - self.observation.request["startedMonotonic"],
        )
        self.folder = self.observation.start(
            [
                self.docker,
                "events",
                "--since",
                f"{self.since:.9f}",
                "--until",
                f"{self.until:.9f}",
                "--filter",
                "type=container",
                "--filter",
                f"image={self.image_reference}",
                "--format",
                "{{json .}}",
            ]
        )
        self.offset, self.partial = 0, b""

    def poll(self, *, renew=True) -> None:
        assert self.folder is not None
        path = self.folder / "stdout.private.log"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
            self.offset += len(data)
            lines = (self.partial + data).split(b"\n")
            self.partial = lines.pop()
            for line in lines:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ObservationIncomplete("event_not_object")
                self.events.put_nowait(payload)
        result = resume(self.folder)
        if result["status"] == "unresolved":
            raise ObservationIncomplete(result["reason"])
        if result["status"] == "complete":
            if result["code"] != 0 or any(result["truncated"].values()) or self.partial:
                raise ObservationIncomplete("event_capture_incomplete")
            if (
                renew
                and self.until
                < self.observation.request["startedEpoch"]
                + self.observation.deadline
                - self.observation.request["startedMonotonic"]
            ):
                self.since = self.until
                self.start()


def safe_metadata(args: argparse.Namespace) -> dict[str, str | None]:
    result = {field: os.environ.get(name) for field, name in SAFE_ENV.items()}
    for field in SAFE_ENV:
        explicit = getattr(args, field, None)
        if explicit is not None:
            result[field] = explicit
    return result


def write_summary(profile: dict) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    memory = profile["memory"]
    peak = memory["peak_bytes"]
    ratio = memory["peak_limit_ratio"]
    peak_text = "unknown" if peak is None else f"{peak / 1024 / 1024:.1f} MiB"
    if ratio is not None:
        peak_text += f" ({ratio:.1%})"
    image_size = profile["image"]["size_bytes"]
    image_text = (
        "unknown" if image_size is None else f"{image_size / 1024 / 1024:.1f} MiB"
    )
    try:
        with Path(target).open("a", encoding="utf-8") as handle:
            handle.write(f"### Docker action profile: {profile['phase']}\n\n")
            handle.write(
                "| Duration | Pull | Seed | Mean CPU | Peak memory | Image | Exit | Observer |\n"
            )
            handle.write("|---:|---:|---|---:|---:|---:|---:|---|\n")
            handle.write(
                f"| {profile['duration_seconds']:.3f}s | "
                f"{profile['timing']['action_pull_seconds']:.3f}s | "
                f"{profile['seed']['result']} | "
                f"{profile['cpu']['mean_utilization_ratio']:.3f} | {peak_text} | "
                f"{image_text} | {profile['exit']['code']} | "
                f"{profile['observer']['result']} |\n"
            )
    except OSError:
        # Evidence is authoritative; a best-effort GitHub summary must not
        # replace the observer's result or prevent its artifact from uploading.
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Use docker-action-profile control for original observer readiness, inspection, cooperative cancellation and closure.",
    )
    parser.add_argument("--name", dest="phase", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-digest")
    parser.add_argument("--expected-action-commit", default=SUPER_LINTER_ACTION_COMMIT)
    parser.add_argument(
        "--expected-manifest-digest", default=SUPER_LINTER_AMD64_MANIFEST
    )
    parser.add_argument(
        "--expected-seed-image", default=os.environ.get("SUPER_LINTER_SEED_IMAGE")
    )
    parser.add_argument(
        "--seed-result-file",
        type=Path,
        default=Path(
            os.environ.get(
                "SUPER_LINTER_SEED_RESULT",
                "/runner-runtime/_temp/super-linter-seed.json",
            )
        ),
    )
    parser.add_argument("--dind-storage", type=Path, default=Path("/var/lib/docker"))
    parser.add_argument(
        "--dind-evidence-file",
        type=Path,
        default=Path("/runner-runtime/_temp/dind-sidecar.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--docker", default="docker", help=argparse.SUPPRESS)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument(
        "--cache-state",
        choices=("auto", "cold", "warm", "unknown"),
        default="auto",
        help="image state before action preparation when known",
    )
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--pair-id")
    for field in SAFE_ENV:
        parser.add_argument(f"--{field.replace('_', '-')}", dest=field)
    return parser


def control_main(argv: list[str]) -> int:
    """Control the verified original observer without signals or submission."""
    parser = argparse.ArgumentParser(description="Control one original Docker observer")
    parser.add_argument(
        "operation", choices=("inspect", "cancel", "wait-ready", "wait-complete")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--expected-action-commit", required=True)
    parser.add_argument("--expected-manifest-digest", required=True)
    parser.add_argument("--wait", type=float, default=0)
    args = parser.parse_args(argv)
    if not math.isfinite(args.wait) or not 0 <= args.wait <= 60:
        parser.error("wait must be finite and between zero and sixty seconds")
    if not SHA256.fullmatch(args.expected_digest) or not SHA256.fullmatch(
        args.expected_manifest_digest
    ):
        parser.error("image identities must be lowercase sha256 digests")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_action_commit):
        parser.error("action identity must be a forty-character lowercase commit")
    output = args.output.absolute()
    root = output.with_name(output.name + ".capture")
    deadline = time.monotonic() + args.wait

    def emit(state: dict, code: int) -> int:
        safe = {
            key: state[key]
            for key in (
                "status",
                "result",
                "reason",
                "code",
                "owner",
                "deadlineMonotonic",
                "cancellationRequested",
            )
            if key in state
        }
        safe["submissionRepeated"] = False
        print(json.dumps(safe, sort_keys=True))
        return code

    while True:
        try:
            request = json.loads((root / "observation.private.json").read_text())
            config = request["config"]
            expected = {
                "phase": args.name,
                "image": args.image,
                "output": str(output),
                "expected_digest": args.expected_digest,
                "expected_action_commit": args.expected_action_commit,
                "expected_manifest_digest": args.expected_manifest_digest,
            }
            if (
                not isinstance(config, dict)
                or not isinstance(config.get("arguments"), dict)
                or config.get("kind") != "docker-action"
                or config.get("cwd") != str(Path.cwd())
                or any(
                    config.get("arguments", {}).get(key) != value
                    for key, value in expected.items()
                )
            ):
                raise ObservationIncomplete("control_expectation_mismatch")
            request, binding = Observation._binding(root, config)
            state = Observation.inspect(root, config)
            if state["status"] == "unresolved":
                return emit(state, 2)
            cancellation = root / "cancel.private.json"
            if cancellation.exists() and json.loads(cancellation.read_text()) != {
                "binding": binding,
                "config": config,
            }:
                raise ObservationIncomplete("observation_cancellation_changed")
            if args.operation == "inspect":
                return emit(state, 0)
            if args.operation == "cancel":
                if state["status"] == "complete":
                    return emit(state, state["code"])
                if not cancellation.exists():
                    try:
                        Observation.request_cancel(root, config)
                    except FileExistsError:
                        if json.loads(cancellation.read_text()) != {
                            "binding": binding,
                            "config": config,
                        }:
                            raise ObservationIncomplete(
                                "observation_cancellation_changed"
                            )
                return emit({**state, "cancellationRequested": True}, 0)
            if args.operation == "wait-ready" and state["status"] == "pending":
                marker_path = Path(config["arguments"]["ready_file"])
                if marker_path.exists():
                    if marker_path.is_symlink():
                        raise ObservationIncomplete("observation_marker_changed")
                    marker = json.loads(marker_path.read_text())
                    if marker != {
                        "status": "ready",
                        "binding": binding,
                        "owner": binding["owner"],
                        "capture": str(root),
                    }:
                        raise ObservationIncomplete("observation_marker_changed")
                    return emit({**state, "status": "ready"}, 0)
            if args.operation == "wait-complete" and state["status"] == "complete":
                commands = json.loads((root / "commands.private.json").read_text())[
                    "commands"
                ]
                captures = [
                    resume(root / entry["folder"], expected_argv=entry["argv"])
                    for entry in commands
                ]
                closed = original_process_absent(binding["owner"]) and all(
                    capture["status"] == "complete"
                    and capture.get("withinDeadline")
                    and not any(capture.get("truncated", {}).values())
                    and all(
                        original_process_absent(capture[role])
                        for role in ("worker", "child")
                    )
                    for capture in captures
                )
                if closed and Observation.inspect(root, config) == state:
                    return emit(state, state["code"])
                state = {"status": "pending", "reason": "original_processes_not_closed"}
        except FileNotFoundError:
            state = {"status": "pending", "reason": "original_binding_not_available"}
        except ObservationIncomplete as error:
            return emit({"status": "unresolved", "reason": str(error)}, 2)
        except (OSError, KeyError, ValueError, TypeError):
            return emit(
                {"status": "unresolved", "reason": "control_evidence_incomplete"}, 2
            )
        if time.monotonic() >= deadline:
            return emit(state, 2)
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    selected = sys.argv[1:] if argv is None else argv
    if selected and selected[0] == "control":
        return control_main(selected[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.timeout <= 0:
        parser.error("interval and timeout must be positive")
    if args.expected_digest and not SHA256.fullmatch(args.expected_digest):
        parser.error("expected digest must be sha256:<64 lowercase hex characters>")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_action_commit):
        parser.error(
            "expected action commit must be 40 lowercase hexadecimal characters"
        )
    if not SHA256.fullmatch(args.expected_manifest_digest):
        parser.error(
            "expected manifest digest must be sha256:<64 lowercase hex characters>"
        )

    started_at = datetime.now(UTC)
    config = {
        "kind": "docker-action",
        "arguments": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "cwd": str(Path.cwd()),
    }
    capture_root = args.output.with_name(args.output.name + ".capture")
    if capture_root.exists():
        return Observation.inspect(capture_root, config).get("code", 2)
    if args.output.exists():
        raise FileExistsError(args.output)
    observation = Observation(
        capture_root, config, args.timeout, sources=[Path(__file__)]
    )
    started = observation.request["startedMonotonic"]
    seed = seed_evidence(
        args.seed_result_file,
        args.expected_action_commit,
        args.expected_digest or SUPER_LINTER_INDEX_DIGEST,
        args.expected_manifest_digest,
        args.expected_seed_image,
    )
    dind_start = read_dind_evidence(args.dind_evidence_file)
    stop_requested = False

    def stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous = {
        signum: signal.signal(signum, stop)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
    }
    reader: EventReader | None = None
    observer_result = "profiler_error"
    observer_detail = "initialization_failed"
    selected_id: str | None = None
    exit_code: int | None = None
    exit_signal: int | None = None
    oom = False
    samples = 0
    cpu_sum = 0.0
    cpu_peak = 0.0
    cpu_seconds = 0.0
    memory_peak: int | None = None
    memory_limit: int | None = None
    block_read = block_write = network_receive = network_transmit = 0
    pids_peak: int | None = None
    cache_state = (
        ("warm" if seed["qualified"] else "cold")
        if args.cache_state == "auto"
        else args.cache_state
    )
    image = {"id": None, "digest": None, "size_bytes": None}
    baseline: set[str] = set()
    wait_observed_at: float | None = None
    selected_created: str | None = None
    state_exit: int | None = None
    die_observed = False
    last_sample = time.monotonic()
    image_available_at: float | None = None
    selected_at: float | None = None
    dind_end: dict | None = None
    return_code = 2

    try:
        try:
            image = image_identity(
                args.docker, args.image, args.expected_digest, observation
            )
            image_available_at = time.monotonic()
        except (
            subprocess.SubprocessError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            if args.cache_state == "auto":
                cache_state = "cold"
            deadline = min(observation.deadline, started + 300)
            while time.monotonic() < deadline and not stop_requested:
                try:
                    image = image_identity(
                        args.docker, args.image, args.expected_digest, observation
                    )
                    image_available_at = time.monotonic()
                    break
                except (
                    subprocess.SubprocessError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    time.sleep(args.interval)
            else:
                raise RuntimeError("selected image did not become available")
        baseline = existing_containers(args.docker, str(image["id"]), observation)
        reader = EventReader(args.docker, args.image, observation)
        reader.start()
        # Give the Docker CLI time to subscribe before advertising readiness.
        time.sleep(min(0.25, max(0.05, args.interval)))
        if resume(reader.folder)["status"] == "unresolved":
            raise RuntimeError("Docker event subscription exited before readiness")
        if args.pid_file:
            observation.publish_marker(args.pid_file, status="observing")
        if args.ready_file:
            observation.publish_marker(args.ready_file, status="ready")

        while time.monotonic() - started < args.timeout:
            if stop_requested or observation.cancel_requested():
                observer_result, observer_detail = (
                    "cancelled",
                    "observer_cancel_requested",
                )
                break
            reader.poll()
            while True:
                try:
                    event = reader.events.get_nowait()
                except queue.Empty:
                    break
                action, container_id, attributes = event_fields(event)
                if not container_id or container_id in baseline:
                    continue
                if action in {"create", "start"}:
                    if selected_id and selected_id != container_id:
                        observer_result = "ambiguous"
                        observer_detail = "multiple_matching_containers"
                        stop_requested = True
                        break
                    if not selected_id:
                        details = run_json(
                            args.docker,
                            [
                                "container",
                                "inspect",
                                "--format",
                                "{{json .}}",
                                container_id,
                            ],
                            observation,
                        )
                        if (
                            details.get("Image") != image["id"]
                            or details.get("Id") != container_id
                            or not isinstance(details.get("Created"), str)
                        ):
                            raise RuntimeError(
                                "matching event failed immutable image verification"
                            )
                        selected_id = container_id
                        selected_created = details["Created"]
                        selected_at = time.monotonic()
                        # CPU integration begins with the selected container,
                        # excluding any time spent waiting for late creation.
                        last_sample = time.monotonic()
                elif selected_id == container_id and action == "oom":
                    oom = True
                elif selected_id == container_id and action == "die":
                    die_observed = True
                    value = attributes.get("exitCode")
                    if isinstance(value, str) and value.isdigit():
                        exit_code = int(value)

            if selected_id and exit_code is None:
                try:
                    stats = run_json(
                        args.docker,
                        ["stats", "--no-stream", "--format", "{{json .}}", selected_id],
                        observation,
                        allow_failure=True,
                    )
                except (
                    subprocess.SubprocessError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    stats = {}
                if stats:
                    try:
                        cpu = parse_percent(str(stats["CPUPerc"]))
                        memory, limit = parse_pair(str(stats["MemUsage"]))
                        net_in, net_out = parse_pair(str(stats["NetIO"]))
                        block_in, block_out = parse_pair(str(stats["BlockIO"]))
                        pids = int(stats["PIDs"])
                    except (KeyError, TypeError, ValueError):
                        # Docker may emit transient "--" fields as a container
                        # starts or exits. Skip only that sample, not the event stream.
                        stats = {}
                if stats:
                    observed_dind = read_dind_evidence(args.dind_evidence_file)
                    if observed_dind is not None:
                        dind_end = observed_dind
                    now = time.monotonic()
                    elapsed = max(0.0, now - last_sample)
                    samples += 1
                    cpu_sum += cpu
                    cpu_peak = max(cpu_peak, cpu)
                    cpu_seconds += cpu * elapsed
                    memory_peak = max(memory_peak or 0, memory)
                    # Docker reports an unbounded or unavailable memory limit as
                    # 0B on some cgroup configurations. The profile contract uses
                    # null for that state and positive integers for real limits.
                    if limit > 0:
                        memory_limit = limit
                    network_receive = max(network_receive, net_in)
                    network_transmit = max(network_transmit, net_out)
                    block_read = max(block_read, block_in)
                    block_write = max(block_write, block_out)
                    pids_peak = max(pids_peak or 0, pids)
                    last_sample = now
            if selected_id:
                details = run_json(
                    args.docker,
                    ["container", "inspect", "--format", "{{json .}}", selected_id],
                    observation,
                )
                if (
                    details.get("Id") != selected_id
                    or details.get("Image") != image["id"]
                    or details.get("Created") != selected_created
                ):
                    raise ObservationIncomplete("selected_container_identity_changed")
                state = details.get("State", {})
                if state.get("Running") is False and state.get("Status") == "exited":
                    value = state.get("ExitCode")
                    if (
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or not 0 <= value <= 255
                        or not state.get("FinishedAt")
                    ):
                        raise ObservationIncomplete("container_exit_state_incomplete")
                    if exit_code is not None and exit_code != value:
                        raise ObservationIncomplete("container_exit_evidence_conflicts")
                    state_exit = exit_code = value
                    oom = oom or state.get("OOMKilled") is True
                    if wait_observed_at is None:
                        wait_observed_at = time.monotonic()
            if state_exit is not None and wait_observed_at is not None:
                drained = die_observed or time.monotonic() - wait_observed_at >= 1.0
                if exit_code is not None and drained:
                    observer_result = "completed"
                    observer_detail = "container_exit_observed"
                    return_code = 0
                    break
            if stop_requested or observation.cancel_requested():
                if observer_result != "ambiguous":
                    observer_result = "cancelled"
                    observer_detail = "observer_cancel_requested"
                break
            time.sleep(args.interval)
        else:
            observer_result = "timed_out"
            observer_detail = "container_exit_not_observed"
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        if observation.cancel_requested():
            observer_result, observer_detail = "cancelled", "observer_cancel_requested"
        else:
            observer_result = "profiler_error"
            observer_detail = "docker_observation_failed"
        atomic_json(
            capture_root / "observation-error.private.json",
            {
                "type": type(error).__name__,
                "reason": str(error),
                "traceback": traceback.format_exc(),
            },
        )
    finally:
        observed_outcome = time.monotonic()
        observed_wall = datetime.now(UTC)
        closed = observation.close(budget=5)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if not closed:
        return_code = 2
        observer_result, observer_detail = "unresolved", "original_commands_not_closed"

    duration = max(0.0, observed_outcome - started)
    completed_monotonic = observed_outcome
    if selected_id and dind_end is None:
        dind_end = read_dind_evidence(args.dind_evidence_file)
    if exit_code is not None and exit_code > 128:
        exit_signal = exit_code - 128
    peak_ratio = (
        memory_peak / memory_limit
        if memory_peak is not None and memory_limit not in (None, 0)
        else None
    )
    profile = {
        "schema_version": SCHEMA_VERSION,
        "profile_kind": "docker_action",
        **safe_metadata(args),
        "phase": args.phase,
        "variant": args.variant,
        "pair_id": args.pair_id,
        "cache_state": cache_state,
        "started_at": started_at.isoformat(),
        "completed_at": observed_wall.isoformat(),
        "duration_seconds": duration,
        "sample_count": samples,
        "seed": seed,
        "timing": {
            "seed_load_seconds": seed["load_duration_seconds"],
            "action_pull_seconds": max(
                0.0, (image_available_at or completed_monotonic) - started
            ),
            "container_seconds": max(0.0, completed_monotonic - selected_at)
            if selected_at is not None
            else None,
        },
        "image": image,
        "cpu": {
            "usage_seconds": cpu_seconds,
            "mean_utilization_ratio": cpu_sum / samples if samples else 0.0,
            "peak_utilization_ratio": cpu_peak,
        },
        "memory": {
            "peak_bytes": memory_peak,
            "limit_bytes": memory_limit,
            "peak_limit_ratio": peak_ratio,
            "oom": oom,
        },
        "block_io": {"read_bytes": block_read, "write_bytes": block_write},
        "network_io": {
            "receive_bytes": network_receive,
            "transmit_bytes": network_transmit,
        },
        "pids": {"peak": pids_peak},
        "dind": dind_delta(dind_start, dind_end, args.dind_storage),
        "exit": {"code": exit_code, "signal": exit_signal},
        "observer": {"result": observer_result, "detail": observer_detail},
    }
    atomic_json(args.output, profile)
    outcome = observation.finish(
        args.output,
        code=return_code,
        result=observer_result,
        observed_at=observed_outcome,
    )
    write_summary(profile)
    return outcome["code"]


if __name__ == "__main__":
    raise SystemExit(main())
