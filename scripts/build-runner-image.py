#!/usr/bin/env python3
"""Run the unchanged no-cache build while retaining bounded host diagnostics."""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time

from profile_capture import Observation, resume


def shell_exit_status(code: int) -> int:
    return code if code >= 0 else 128 - code


def numbers(path: Path, selected: set[str] | None = None) -> dict:
    try:
        result = {}
        for line in path.read_text().splitlines():
            parts = line.replace(":", "").split()
            if len(parts) < 2:
                continue
            key, value, *_ = parts
            if (selected is None or key in selected) and value.isdigit():
                result[key] = int(value)
        return result
    except (OSError, ValueError) as error:
        return {"unavailable": type(error).__name__}


def host_snapshot() -> dict:
    data = {
        "memoryKiB": numbers(
            Path("/proc/meminfo"), {"MemAvailable", "MemFree", "SwapFree"}
        ),
        "vmstat": numbers(Path("/proc/vmstat"), {"oom_kill"}),
        "disks": {},
        "processes": [],
        "cgroup": {},
    }
    for name, location in [
        ("root", "/"),
        ("docker", "/var/lib/docker"),
        ("runnerTemp", os.environ.get("RUNNER_TEMP")),
    ]:
        if not location:
            continue
        try:
            usage = shutil.disk_usage(location)
            data["disks"][name] = {"totalBytes": usage.total, "freeBytes": usage.free}
        except OSError as error:
            data["disks"][name] = {"unavailable": type(error).__name__}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / "comm").read_text().strip()
            if name in {"dockerd", "docker", "buildkitd", "containerd"}:
                data["processes"].append(
                    {
                        "pid": int(proc.name),
                        "name": name,
                        "status": numbers(
                            proc / "status", {"VmRSS", "VmHWM", "VmSize", "PPid"}
                        ),
                    }
                )
        except OSError:
            continue
        if len(data["processes"]) == 64:
            break
    try:
        entries = Path("/proc/self/cgroup").read_text().splitlines()
        unified = next(line[3:] for line in entries if line.startswith("0::"))
        base = Path("/sys/fs/cgroup")
        path = base / unified.lstrip("/")
        for _ in range(8):
            if not path.is_relative_to(base):
                break
            group = {"events": numbers(path / "memory.events")}
            for filename in ["memory.current", "memory.peak", "memory.max"]:
                try:
                    group[filename] = (path / filename).read_text().strip()
                except OSError as error:
                    group[filename] = {"unavailable": type(error).__name__}
            data["cgroup"][str(path.relative_to(base))] = group
            if path == base:
                break
            path = path.parent
    except (OSError, StopIteration) as error:
        data["cgroup"] = {"unavailable": type(error).__name__}
    return data


def run_monitored(
    command: list[str],
    emit,
    interval: float = 15.0,
    sample=host_snapshot,
    *,
    capture_root: Path | None = None,
    timeout: float = 8400,
    output: Path | None = None,
) -> int:
    if interval <= 0 or interval > 60:
        raise ValueError("Diagnostic interval must be positive and at most 60 seconds")
    fixture_parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
    if capture_root is None and fixture_parent:
        Path(fixture_parent).mkdir(parents=True, exist_ok=True)
    root = (
        capture_root
        or Path(tempfile.mkdtemp(prefix="runner-build-", dir=fixture_parent))
        / "capture"
    )
    config = {
        "kind": "runner-build",
        "command": command,
        "cwd": str(Path.cwd()),
        "timeout": timeout,
        "interval": interval,
        "output": str(output.absolute()) if output else None,
    }
    if root.exists():
        prior = Observation.inspect(root, config)
        return prior.get("code", 2)
    observation = Observation(root, config, timeout, sources=[Path(__file__)])
    output = output or root / "build-outcome.private.json"
    started = observation.request["startedMonotonic"]
    received_signal = None

    def report(event: str, **fields):
        emit(
            {
                "event": event,
                "elapsedSeconds": time.monotonic() - started,
                "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                **fields,
            }
        )

    report("start", host=sample())
    child = observation.start(command)
    offsets = {"stdout": 0, "stderr": 0}

    def copy_logs():
        for name, stream in [("stdout", sys.stdout), ("stderr", sys.stderr)]:
            path = child / f"{name}.private.log"
            if path.exists():
                with path.open("rb") as log:
                    log.seek(offsets[name])
                    data = log.read()
                offsets[name] += len(data)
                stream.write(data.decode("utf-8", errors="replace"))
                stream.flush()

    def interrupted(signum, _frame):
        nonlocal received_signal
        received_signal = signum
        report("signal", signal=signum)

    previous = {
        sig: signal.signal(sig, interrupted)
        for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP]
    }
    observed = None
    next_sample = started + interval
    result = {"status": "pending"}
    try:
        while time.monotonic() < observation.deadline:
            copy_logs()
            result = resume(child)
            if result["status"] != "pending":
                observed = time.monotonic()
                break
            if received_signal is not None or observation.cancel_requested():
                observed = time.monotonic()
                break
            if time.monotonic() >= next_sample:
                report("sample", host=sample())
                next_sample += interval
            time.sleep(min(0.02, max(0, observation.deadline - time.monotonic())))
    finally:
        closed = observation.close(budget=5)
        result = resume(child)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    code = result.get("code")
    exit_code = shell_exit_status(code) if code is not None else 2
    state = "completed" if code == 0 else "failed"
    if received_signal is not None:
        exit_code, state = 128 + received_signal, "cancelled"
    elif observation.cancel_requested():
        exit_code, state = 143, "cancelled"
    if (
        not closed
        or result["status"] != "complete"
        or not result.get("withinDeadline")
        or any(result.get("truncated", {}).values())
    ):
        exit_code, state = exit_code or 2, "unresolved"
    final = {
        "childReturnCode": code,
        "exitCode": exit_code,
        "result": state,
        "host": sample(),
    }
    # Emit the effective outcome only after closure; late completion cannot change it.
    report("exit", **final)
    if output == root / "build-outcome.private.json":
        output.write_text(json.dumps(final))
    outcome = observation.finish(
        output, code=exit_code, result=state, observed_at=observed
    )
    copy_logs()
    return outcome["code"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", choices=["standard", "container-build"], required=True
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=8400)
    args = parser.parse_args()
    os.umask(0o077)
    args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    capture_root = args.diagnostics.with_name(args.diagnostics.name + ".capture")
    if capture_root.exists():
        config = {
            "kind": "runner-build",
            "command": [
                "docker",
                "buildx",
                "build",
                "--progress=plain",
                "--no-cache",
                "--load",
                "--target",
                args.target,
                "--tag",
                args.tag,
                ".",
            ],
            "cwd": str(Path.cwd()),
            "timeout": args.timeout,
            "interval": 15.0,
            "output": str(args.diagnostics.absolute()),
        }
        return Observation.inspect(capture_root, config).get("code", 2)
    with args.diagnostics.open("x", encoding="utf-8") as output:

        def emit(record):
            # Build stdout remains a log stream; this separate file is strict JSONL.
            text = json.dumps(record, sort_keys=True)
            with lock:
                output.write(text + "\n")
                output.flush()
                print("RUNNER_BUILD_DIAGNOSTICS " + text, flush=True)

        return run_monitored(
            [
                "docker",
                "buildx",
                "build",
                "--progress=plain",
                "--no-cache",
                "--load",
                "--target",
                args.target,
                "--tag",
                args.tag,
                ".",
            ],
            emit,
            capture_root=capture_root,
            timeout=args.timeout,
            output=args.diagnostics,
        )


if __name__ == "__main__":
    sys.exit(main())
