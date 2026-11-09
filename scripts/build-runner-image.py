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
import subprocess
import sys
import threading
import time


def numbers(path: Path, selected: set[str] | None = None) -> dict:
    try:
        result = {}
        for line in path.read_text().splitlines():
            key, value, *_ = line.replace(":", "").split()
            if (selected is None or key in selected) and value.isdigit():
                result[key] = int(value)
        return result
    except (OSError, ValueError) as error:
        return {"unavailable": type(error).__name__}


def host_snapshot() -> dict:
    data = {
        "memoryKiB": numbers(Path("/proc/meminfo"), {"MemAvailable", "MemFree", "SwapFree"}),
        "vmstat": numbers(Path("/proc/vmstat"), {"oom_kill"}),
        "disks": {},
        "processes": [],
        "cgroup": {},
    }
    for name, location in [("root", "/"), ("docker", "/var/lib/docker"),
                           ("runnerTemp", os.environ.get("RUNNER_TEMP"))]:
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
                data["processes"].append({"pid": int(proc.name), "name": name,
                    "status": numbers(proc / "status", {"VmRSS", "VmHWM", "VmSize", "PPid"})})
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


def run_monitored(command: list[str], emit, interval: float = 15.0, sample=host_snapshot) -> int:
    if interval <= 0 or interval > 60:
        raise ValueError("Diagnostic interval must be positive and at most 60 seconds")
    stop = threading.Event()
    started = time.monotonic()
    received_signal = None

    def report(event: str, **fields):
        emit({"event": event, "elapsedSeconds": time.monotonic() - started,
              "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields})

    report("start", host=sample())
    child = subprocess.Popen(command)

    def observe():
        while not stop.wait(interval):
            report("sample", host=sample())

    def interrupted(signum, _frame):
        nonlocal received_signal
        received_signal = signum
        report("signal", signal=signum)
        if child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass

    previous = {sig: signal.signal(sig, interrupted) for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP]}
    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    try:
        code = child.wait()
    finally:
        stop.set()
        observer.join(timeout=2)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    # Preserve the shell convention for a child terminated by a signal.
    result = code if code >= 0 else 128 - code
    if received_signal is not None:
        result = 128 + received_signal
    report("exit", childReturnCode=code, exitCode=result, host=sample())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["standard", "container-build"], required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    with args.diagnostics.open("x", encoding="utf-8") as output:
        def emit(record):
            # Build stdout remains a log stream; this separate file is strict JSONL.
            text = json.dumps(record, sort_keys=True)
            with lock:
                output.write(text + "\n")
                output.flush()
                print("RUNNER_BUILD_DIAGNOSTICS " + text, flush=True)
        return run_monitored(["docker", "buildx", "build", "--progress=plain", "--no-cache",
                              "--load", "--target", args.target, "--tag", args.tag, "."], emit)


if __name__ == "__main__":
    sys.exit(main())
