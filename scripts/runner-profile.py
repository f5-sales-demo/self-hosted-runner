#!/usr/bin/env python3
"""Run one named phase and record a redacted cgroup-v2 workload profile."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
SAFE_ENV = {
    "repository": "GITHUB_REPOSITORY",
    "commit": "GITHUB_SHA",
    "run_id": "GITHUB_RUN_ID",
    "run_attempt": "GITHUB_RUN_ATTEMPT",
    "job_id": "GITHUB_JOB",
    "runner_name": "RUNNER_NAME",
    "runner_profile": "RUNNER_PROFILE",
    "image_digest": "RUNNER_IMAGE_DIGEST",
}


def parse_flat(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return result
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return result


def parse_io(path: Path) -> dict[str, int]:
    totals: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return totals
    for line in lines:
        for field in line.split()[1:]:
            key, separator, value = field.partition("=")
            if not separator:
                continue
            try:
                totals[key] = totals.get(key, 0) + int(value)
            except ValueError:
                continue
    return totals


def read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None


def cgroup_path(root: Path) -> Path:
    if root != Path("/sys/fs/cgroup"):
        return root
    try:
        entries = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    except OSError:
        return root
    unified = next(
        (line.split(":", 2)[2] for line in entries if line.startswith("0::")), "/"
    )
    candidate = (root / unified.lstrip("/")).resolve()
    return candidate if root.resolve() in (candidate, *candidate.parents) else root


def cpu_limit(cgroup: Path) -> float | None:
    try:
        quota, period = (cgroup / "cpu.max").read_text(encoding="utf-8").split()
        if quota == "max":
            return float(os.cpu_count() or 1)
        return int(quota) / int(period)
    except (FileNotFoundError, PermissionError, OSError, ValueError, ZeroDivisionError):
        return None


def snapshot(cgroup: Path) -> dict:
    return {
        "cpu": parse_flat(cgroup / "cpu.stat"),
        "memory_current": read_int(cgroup / "memory.current"),
        "memory_peak": read_int(cgroup / "memory.peak"),
        "memory_limit": read_int(cgroup / "memory.max"),
        "memory_events": parse_flat(cgroup / "memory.events"),
        "io": parse_io(cgroup / "io.stat"),
    }


def delta(
    after: dict[str, int], before: dict[str, int], keys: tuple[str, ...]
) -> dict[str, int]:
    return {key: max(0, after.get(key, 0) - before.get(key, 0)) for key in keys}


class Sampler:
    def __init__(self, cgroup: Path, interval: float):
        self.cgroup = cgroup
        self.interval = interval
        self.stop_event = threading.Event()
        self.peak = 0
        self.samples = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            value = read_int(self.cgroup / "memory.current")
            if value is not None:
                self.peak = max(self.peak, value)
            self.samples += 1
            self.stop_event.wait(self.interval)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(1.0, self.interval * 2))


def safe_metadata(args: argparse.Namespace) -> dict[str, str | None]:
    values = {field: os.environ.get(name) for field, name in SAFE_ENV.items()}
    for field in SAFE_ENV:
        explicit = getattr(args, field, None)
        if explicit is not None:
            values[field] = explicit
    image = values["image_digest"]
    if image and "@sha256:" in image:
        values["image_digest"] = image.rsplit("@", 1)[1]
    return values


def write_summary(path: str | None, profile: dict) -> None:
    if not path:
        return
    memory = profile["memory"]
    peak = memory["peak_bytes"]
    ratio = memory["peak_limit_ratio"]
    peak_text = "unknown"
    if peak is not None:
        peak_text = f"{peak / 1024 / 1024:.1f} MiB"
        if ratio is not None:
            peak_text += f" ({ratio:.1%})"
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"### Workload profile: {profile['phase']}\n\n")
        handle.write(
            "| Duration | CPU utilization | Peak memory | Throttled | Exit |\n"
        )
        handle.write("|---:|---:|---:|---:|---:|\n")
        handle.write(
            f"| {profile['duration_seconds']:.3f}s | "
            f"{profile['cpu']['utilization_ratio']:.3f} | {peak_text} | "
            f"{profile['cpu']['throttled_usec']} us | {profile['exit']['code']} |\n"
        )


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--name", dest="phase", required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--cache-state", choices=("cold", "warm", "unknown"), default="unknown"
    )
    result.add_argument("--variant", default="baseline")
    result.add_argument("--pair-id")
    result.add_argument("--output-digest")
    result.add_argument("--interval", type=float, default=0.25)
    result.add_argument(
        "--cgroup-root",
        type=Path,
        default=Path("/sys/fs/cgroup"),
        help=argparse.SUPPRESS,
    )
    for field in SAFE_ENV:
        result.add_argument(f"--{field.replace('_', '-')}", dest=field)
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main(argv: list[str] | None = None) -> int:
    arg_parser = build_parser()
    args = arg_parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command or args.interval <= 0:
        arg_parser.error("a command and a positive sampling interval are required")
    cgroup = cgroup_path(args.cgroup_root)
    before = snapshot(cgroup)
    sampler = Sampler(cgroup, args.interval)
    sampler.peak = before["memory_current"] or 0
    started_wall = datetime.now(UTC)
    started_mono = time.monotonic()
    child = subprocess.Popen(args.command, start_new_session=True)
    received_signal: list[int] = []

    def forward(signum, _frame):
        received_signal[:] = [signum]
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    handled = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)
    previous = {signum: signal.signal(signum, forward) for signum in handled}
    sampler.start()
    try:
        returncode = child.wait()
    finally:
        sampler.stop()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    duration = time.monotonic() - started_mono
    after = snapshot(cgroup)
    cpu = delta(
        after["cpu"],
        before["cpu"],
        (
            "usage_usec",
            "user_usec",
            "system_usec",
            "nr_periods",
            "nr_throttled",
            "throttled_usec",
        ),
    )
    limit_cpus = cpu_limit(cgroup)
    utilization = (
        cpu["usage_usec"] / 1_000_000 / duration / limit_cpus
        if limit_cpus and duration > 0
        else 0.0
    )
    memory_peak = sampler.peak or after["memory_current"]
    memory_limit = after["memory_limit"]
    termination_signal = (
        -returncode
        if returncode < 0
        else (received_signal[0] if received_signal else None)
    )
    exit_code = 128 + termination_signal if termination_signal else returncode
    profile = {
        "schema_version": SCHEMA_VERSION,
        **safe_metadata(args),
        "phase": args.phase,
        "variant": args.variant,
        "pair_id": args.pair_id,
        "cache_state": args.cache_state,
        "started_at": started_wall.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "duration_seconds": duration,
        "sample_count": sampler.samples,
        "phase_timings": [{"name": args.phase, "duration_seconds": duration}],
        "cpu": {
            "usage_usec": cpu["usage_usec"],
            "user_usec": cpu["user_usec"],
            "system_usec": cpu["system_usec"],
            "utilization_ratio": utilization,
            "nr_periods": cpu["nr_periods"],
            "nr_throttled": cpu["nr_throttled"],
            "throttled_usec": cpu["throttled_usec"],
        },
        "memory": {
            "current_bytes": after["memory_current"],
            "peak_bytes": memory_peak,
            "limit_bytes": memory_limit,
            "peak_limit_ratio": (
                memory_peak / memory_limit
                if memory_peak is not None and memory_limit
                else None
            ),
            "events": delta(
                after["memory_events"],
                before["memory_events"],
                ("low", "high", "max", "oom", "oom_kill"),
            ),
        },
        "io": delta(
            after["io"],
            before["io"],
            ("rbytes", "wbytes", "rios", "wios", "dbytes", "dios"),
        ),
        "output_digest": args.output_digest,
        "exit": {"code": exit_code, "signal": termination_signal},
    }
    atomic_json(args.output, profile)
    write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), profile)
    if termination_signal:
        signal.signal(termination_signal, signal.SIG_DFL)
        os.kill(os.getpid(), termination_signal)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
