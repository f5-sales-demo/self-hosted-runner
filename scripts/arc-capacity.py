#!/usr/bin/env python3
"""Collect and evaluate correlated ARC assignment and workload evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path, PurePosixPath
from statistics import median, quantiles
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "config/arc-capacity.json"
PROFILE_REQUIRED = {
    "schema_version",
    "repository",
    "commit",
    "run_id",
    "run_attempt",
    "job_id",
    "runner_name",
    "runner_profile",
    "image_digest",
    "phase",
    "variant",
    "pair_id",
    "cache_state",
    "started_at",
    "completed_at",
    "duration_seconds",
    "sample_count",
    "phase_timings",
    "cpu",
    "memory",
    "io",
    "output_digest",
    "exit",
}


DOCKER_PROFILE_REQUIRED = {
    "schema_version",
    "profile_kind",
    "repository",
    "commit",
    "run_id",
    "run_attempt",
    "job_id",
    "runner_name",
    "runner_profile",
    "runner_image_digest",
    "phase",
    "variant",
    "pair_id",
    "cache_state",
    "started_at",
    "completed_at",
    "duration_seconds",
    "sample_count",
    "image",
    "cpu",
    "memory",
    "block_io",
    "network_io",
    "pids",
    "exit",
    "observer",
}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
NODE_FILESYSTEM_REQUIRED = {
    "filesystem_bytes",
    "used_bytes",
    "available_bytes",
    "used_ratio",
}
BURST_PATTERNS = {
    "baseline": re.compile(r"^Current D16 two-slot burst / slot-[1-4]$"),
    "d16-four": re.compile(r"^Candidate D16 four-slot burst / slot-[1-4]$"),
    "f32": re.compile(r"^F32 four-job burst / slot-[1-4]$"),
}
BURST_COST_MODEL = {
    "baseline": {"sku": "d16", "runners_per_node": 1, "peak_nodes": 5},
    "d16-four": {"sku": "d16", "runners_per_node": 1, "peak_nodes": 9},
    "f32": {"sku": "f32", "runners_per_node": 2, "peak_nodes": 5},
}
MAX_SUSTAINED_THROTTLE_REGRESSION_RATIO = 0.01


def _integer_map(value: object, name: str) -> dict:
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and isinstance(counter, int)
        and not isinstance(counter, bool)
        and counter >= 0
        for key, counter in value.items()
    ):
        raise TypeError(f"{name} must contain nonnegative integer counters")
    return value


def _nonnegative_number(value: object, name: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise TypeError(f"{name} must be a nonnegative number")
    return value


def validate_docker_action_profile(profile: object) -> dict:
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("unsupported Docker action profile")
    if set(profile) != DOCKER_PROFILE_REQUIRED:
        raise ValueError("Docker action profile fields do not match schema version 1")
    if profile.get("profile_kind") != "docker_action":
        raise ValueError("invalid Docker action profile kind")
    nullable_strings = (
        "repository",
        "commit",
        "run_id",
        "run_attempt",
        "job_id",
        "runner_name",
        "runner_profile",
        "runner_image_digest",
        "pair_id",
    )
    if any(
        profile[key] is not None and not isinstance(profile[key], str)
        for key in nullable_strings
    ):
        raise TypeError("Docker action identity fields must be strings or null")
    if any(
        not isinstance(profile[key], str) or not profile[key]
        for key in ("phase", "variant", "started_at", "completed_at")
    ):
        raise TypeError("Docker action phase, variant, and timestamps must be strings")
    parse_time(profile["started_at"])
    parse_time(profile["completed_at"])
    if profile["cache_state"] not in {"cold", "warm", "unknown"}:
        raise ValueError("invalid Docker image cache state")
    _nonnegative_number(profile["duration_seconds"], "Docker action duration")
    sample_count = profile["sample_count"]
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 0
    ):
        raise TypeError("invalid Docker sample count")
    image = profile["image"]
    if not isinstance(image, dict) or set(image) != {"id", "digest", "size_bytes"}:
        raise TypeError("invalid Docker image identity")
    for key in ("id", "digest"):
        value = image[key]
        if value is not None and (
            not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value)
        ):
            raise TypeError(f"invalid Docker image {key}")
    if image["size_bytes"] is not None:
        _nonnegative_number(image["size_bytes"], "Docker image size")
        if not isinstance(image["size_bytes"], int):
            raise TypeError("Docker image size must be an integer")
    cpu = profile["cpu"]
    if not isinstance(cpu, dict) or set(cpu) != {
        "usage_seconds",
        "mean_utilization_ratio",
        "peak_utilization_ratio",
    }:
        raise TypeError("invalid Docker CPU metrics")
    for key, value in cpu.items():
        _nonnegative_number(value, f"Docker CPU {key}")
    memory = profile["memory"]
    if not isinstance(memory, dict) or set(memory) != {
        "peak_bytes",
        "limit_bytes",
        "peak_limit_ratio",
        "oom",
    }:
        raise TypeError("invalid Docker memory metrics")
    for key in ("peak_bytes", "limit_bytes"):
        value = memory[key]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise TypeError(f"invalid Docker memory {key}")
    if memory["peak_limit_ratio"] is not None:
        _nonnegative_number(memory["peak_limit_ratio"], "Docker memory ratio")
    if not isinstance(memory["oom"], bool):
        raise TypeError("invalid Docker OOM state")
    counter_keys = {
        "block_io": {"read_bytes", "write_bytes"},
        "network_io": {"receive_bytes", "transmit_bytes"},
        "pids": {"peak"},
    }
    for name, required_keys in counter_keys.items():
        counters = profile[name]
        if not isinstance(counters, dict) or set(counters) != required_keys:
            raise TypeError(f"invalid Docker {name} counters")
        _integer_map(counters, f"Docker {name}")
    exit_status = profile["exit"]
    if not isinstance(exit_status, dict) or set(exit_status) != {"code", "signal"}:
        raise TypeError("invalid Docker action exit")
    code = exit_status["code"]
    observed_signal = exit_status["signal"]
    if code is not None and (
        isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 255
    ):
        raise TypeError("invalid Docker action exit code")
    if observed_signal is not None and (
        isinstance(observed_signal, bool)
        or not isinstance(observed_signal, int)
        or observed_signal < 1
    ):
        raise TypeError("invalid Docker action signal")
    observer = profile["observer"]
    if not isinstance(observer, dict) or set(observer) != {"result", "detail"}:
        raise TypeError("invalid Docker observer result")
    results = {"completed", "cancelled", "timed_out", "ambiguous", "profiler_error"}
    details = {
        "container_exit_observed",
        "observer_signal",
        "container_exit_not_observed",
        "multiple_matching_containers",
        "initialization_failed",
        "docker_observation_failed",
    }
    if observer["result"] not in results or observer["detail"] not in details:
        raise ValueError("unknown Docker observer result")
    if observer["result"] == "completed" and (
        code is None or image["id"] is None or image["digest"] is None
    ):
        raise ValueError("completed Docker profile lacks immutable identity or exit")
    return profile


def validate_workload_profile(profile: object) -> dict:
    if isinstance(profile, dict) and profile.get("profile_kind") == "docker_action":
        return validate_docker_action_profile(profile)
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise ValueError("unsupported workload profile")
    if set(profile) != PROFILE_REQUIRED:
        raise ValueError("workload profile fields do not match schema version 1")
    nullable_strings = (
        "repository",
        "commit",
        "run_id",
        "run_attempt",
        "job_id",
        "runner_name",
        "runner_profile",
        "image_digest",
        "pair_id",
        "output_digest",
    )
    if any(
        profile[key] is not None and not isinstance(profile[key], str)
        for key in nullable_strings
    ):
        raise TypeError("workload identity fields must be strings or null")
    if any(
        not isinstance(profile[key], str) or not profile[key]
        for key in ("phase", "variant", "started_at", "completed_at")
    ):
        raise TypeError(
            "workload phase, variant, and timestamps must be nonempty strings"
        )
    parse_time(profile["started_at"])
    parse_time(profile["completed_at"])
    if profile["cache_state"] not in {"cold", "warm", "unknown"}:
        raise ValueError("invalid cache state")
    duration = profile["duration_seconds"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
    ):
        raise ValueError("invalid workload duration")
    sample_count = profile["sample_count"]
    if (
        isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 1
    ):
        raise ValueError("invalid sample count")
    timings = profile["phase_timings"]
    if not isinstance(timings, list) or any(
        not isinstance(timing, dict)
        or set(timing) != {"name", "duration_seconds"}
        or not isinstance(timing["name"], str)
        or not timing["name"]
        or isinstance(timing["duration_seconds"], bool)
        or not isinstance(timing["duration_seconds"], (int, float))
        or timing["duration_seconds"] < 0
        for timing in timings
    ):
        raise TypeError("invalid phase timings")
    cpu = profile["cpu"]
    cpu_keys = {
        "usage_usec",
        "user_usec",
        "system_usec",
        "utilization_ratio",
        "nr_periods",
        "nr_throttled",
        "throttled_usec",
    }
    if not isinstance(cpu, dict) or set(cpu) != cpu_keys:
        raise TypeError("invalid CPU counters")
    _integer_map(
        {key: value for key, value in cpu.items() if key != "utilization_ratio"}, "CPU"
    )
    utilization = cpu["utilization_ratio"]
    if (
        isinstance(utilization, bool)
        or not isinstance(utilization, (int, float))
        or utilization < 0
    ):
        raise TypeError("invalid CPU utilization")
    memory = profile["memory"]
    if not isinstance(memory, dict) or set(memory) != {
        "current_bytes",
        "peak_bytes",
        "limit_bytes",
        "peak_limit_ratio",
        "events",
    }:
        raise TypeError("invalid memory counters")
    for key in ("current_bytes", "peak_bytes", "limit_bytes"):
        value = memory[key]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise TypeError(f"invalid memory counter: {key}")
    ratio = memory["peak_limit_ratio"]
    if ratio is not None and (
        isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or ratio < 0
    ):
        raise TypeError("invalid peak memory ratio")
    _integer_map(memory["events"], "memory events")
    _integer_map(profile["io"], "I/O")
    exit_status = profile["exit"]
    if not isinstance(exit_status, dict) or set(exit_status) != {"code", "signal"}:
        raise TypeError("invalid workload exit status")
    code = exit_status["code"]
    observed_signal = exit_status["signal"]
    if isinstance(code, bool) or not isinstance(code, int) or not 0 <= code <= 255:
        raise TypeError("invalid workload exit code")
    if observed_signal is not None and (
        isinstance(observed_signal, bool)
        or not isinstance(observed_signal, int)
        or observed_signal < 1
    ):
        raise TypeError("invalid workload signal")
    return profile


def validate_node_filesystem(report: object) -> dict:
    if not isinstance(report, dict) or set(report) != NODE_FILESYSTEM_REQUIRED:
        raise TypeError("node filesystem report fields do not match the contract")
    for key in ("filesystem_bytes", "used_bytes", "available_bytes"):
        value = report[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TypeError(f"invalid node filesystem counter: {key}")
    ratio = report["used_ratio"]
    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not 0 <= ratio <= 1
    ):
        raise TypeError("invalid node filesystem used ratio")
    if report["used_bytes"] > report["filesystem_bytes"]:
        raise ValueError("node filesystem used bytes exceed its capacity")
    if report["available_bytes"] > report["filesystem_bytes"]:
        raise ValueError("node filesystem available bytes exceed its capacity")
    return report


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return quantiles(values, n=100, method="inclusive")[94]


def recommend_cap(
    pool_capacity: int, existing: int, peak: int, p95_five_minute: float
) -> int:
    return min(
        pool_capacity, max(existing, peak + 2, math.ceil(1.25 * p95_five_minute))
    )


def classify_warm(
    demanded_at: datetime,
    nodes: list[dict],
    profile: str,
    node_name: str | None = None,
) -> bool:
    for node in nodes:
        if (
            node.get("profile") != profile
            or not node.get("schedulable", False)
            or (node_name and node.get("name") != node_name)
        ):
            continue
        ready = parse_time(node.get("ready_at"))
        removed = parse_time(node.get("removed_at"))
        if (
            ready
            and ready <= demanded_at
            and (removed is None or demanded_at < removed)
        ):
            return True
    return False


def in_service_window(instant: datetime, policy: dict) -> bool:
    local = instant.astimezone(ZoneInfo(policy["timezone"]))
    return (
        policy["service_window"]["start_hour"]
        <= local.hour
        < policy["service_window"]["end_hour"]
    )


def assignment_slo_summary(samples: list[dict], policy: dict) -> list[dict]:
    reports = []
    for kind, warm in (("warm", True), ("cold", False)):
        values = []
        for sample in samples:
            if (
                not sample.get("assignment_slo_eligible", True)
                or sample.get("warm") is not warm
            ):
                continue
            value = sample.get("assignment_seconds")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ):
                values.append(float(value))
        limit = policy["slo_seconds"][f"{kind}_assignment_p95"]
        p95 = percentile95(values)
        reports.append(
            {
                "class": kind,
                "samples": len(values),
                "median_seconds": median(values) if values else None,
                "p95_seconds": p95,
                "limit_seconds": limit,
                "qualifies": p95 is not None and p95 <= limit,
            }
        )
    return reports


def evaluate(
    samples: list[dict], policy: dict, now: datetime, quotas: list[dict] | None = None
) -> dict:
    alerts: list[dict] = []
    buckets: dict[tuple[str, datetime], list[float]] = {}
    interval = policy["sample_minutes"]
    for sample in samples:
        eligible = sample.get("assignment_slo_eligible", True)
        queued = parse_time(sample.get("queued_at"))
        if eligible and queued and sample.get("started_at"):
            assignment = sample.get("assignment_seconds")
            boundary = queued.replace(
                minute=(queued.minute // interval) * interval, second=0, microsecond=0
            )
            warm = sample.get("warm")
            if (
                warm is not None
                and isinstance(assignment, (int, float))
                and not isinstance(assignment, bool)
                and assignment >= 0
            ):
                kind = "warm" if warm else "cold"
                buckets.setdefault((kind, boundary), []).append(float(assignment))
        wait = sample.get("assignment_seconds")
        if (
            eligible
            and wait is not None
            and wait >= policy["alerts"]["job_wait_seconds"]
        ):
            alerts.append(
                {"kind": "job_wait", "job": sample.get("job_id"), "seconds": wait}
            )
        pending = sample.get("pending_at_pool_max_seconds", 0)
        if eligible and pending >= policy["alerts"]["pending_at_pool_max_seconds"]:
            alerts.append(
                {
                    "kind": "pool_saturated",
                    "profile": sample.get("profile"),
                    "seconds": pending,
                }
            )
    for kind in ("warm", "cold"):
        limit = policy["slo_seconds"][f"{kind}_assignment_p95"]
        series = sorted(
            (when, percentile95(values))
            for (bucket_kind, when), values in buckets.items()
            if bucket_kind == kind
        )
        count = policy["consecutive_slo_breaches"]
        recent = series[-count:]
        consecutive = len(recent) == count and all(
            later[0] - earlier[0] == timedelta(minutes=interval)
            for earlier, later in pairwise(recent)
        )
        if consecutive and all(
            value is not None and value > limit for _, value in recent
        ):
            alerts.append(
                {
                    "kind": "assignment_slo",
                    "class": kind,
                    "p95_seconds": series[-1][1],
                    "limit_seconds": limit,
                }
            )
    quota_records = quotas or []
    legacy_quota = next(
        (sample.get("quota") for sample in reversed(samples) if sample.get("quota")),
        None,
    )
    if legacy_quota:
        quota_records = [legacy_quota]
    for quota in quota_records:
        if quota.get("limit", 0) <= 0:
            continue
        headroom = 1 - quota["used"] / quota["limit"]
        if headroom < policy["alerts"]["minimum_quota_headroom_ratio"]:
            alerts.append(
                {
                    "kind": "quota_headroom",
                    "quota": quota.get("name"),
                    "ratio": headroom,
                }
            )
    paging = in_service_window(now, policy)
    return {
        "generated_at": now.astimezone(UTC).isoformat(),
        "paging": paging,
        "alerts": [{**item, "page": paging} for item in alerts],
        "assignment_slo_summary": assignment_slo_summary(samples, policy),
    }


def command_json(args: list[str], optional: bool = False):
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if result.returncode:
        if optional:
            return None
        raise RuntimeError(
            f"command failed ({' '.join(args)}): {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def kubernetes_snapshot() -> dict:
    resources = {}
    for name, command in {
        "nodes": ["kubectl", "get", "nodes", "-o", "json"],
        "pods": ["kubectl", "get", "pods", "-A", "-o", "json"],
        "runner_sets": ["kubectl", "get", "autoscalingrunnersets", "-A", "-o", "json"],
        "events": ["kubectl", "get", "events", "-A", "-o", "json"],
        "node_metrics": ["kubectl", "top", "nodes", "--no-headers"],
        "pod_metrics": ["kubectl", "top", "pods", "-A", "--no-headers"],
    }.items():
        if name.endswith("_metrics"):
            result = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            resources[name] = (
                result.stdout.splitlines() if result.returncode == 0 else []
            )
        else:
            resources[name] = command_json(command)
    resources["azure_quotas"] = (
        command_json(
            [
                "az",
                "vm",
                "list-usage",
                "--location",
                "canadacentral",
                "--output",
                "json",
            ],
            optional=True,
        )
        or []
    )
    return resources


def metric_rows(lines: list[str], namespaced: bool = False) -> dict[str, dict]:
    result = {}
    for line in lines:
        fields = line.split()
        minimum = 4 if namespaced else 5
        if len(fields) < minimum:
            continue
        key = f"{fields[0]}/{fields[1]}" if namespaced else fields[0]
        cpu_index, memory_index = (2, 3) if namespaced else (1, 3)
        result[key] = {"cpu": fields[cpu_index], "memory": fields[memory_index]}
    return result


def summarize_kubernetes(resources: dict) -> dict:
    node_metrics = metric_rows(resources.get("node_metrics", []))
    pod_metrics = metric_rows(resources.get("pod_metrics", []), namespaced=True)
    nodes = []
    nodes_by_name = {}
    for item in resources.get("nodes", {}).get("items", []):
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        ready_condition = next(
            (
                condition
                for condition in status.get("conditions") or []
                if condition.get("type") == "Ready"
            ),
            {},
        )
        node = {
            "name": metadata.get("name"),
            "profile": metadata.get("labels", {}).get("runner-profile"),
            "created_at": metadata.get("creationTimestamp"),
            "ready_at": ready_condition.get("lastTransitionTime")
            if ready_condition.get("status") == "True"
            else None,
            "removed_at": None,
            "schedulable": not item.get("spec", {}).get("unschedulable", False)
            and ready_condition.get("status") == "True",
            "usage": node_metrics.get(metadata.get("name")),
            "allocatable": {
                key: status.get("allocatable", {}).get(key) for key in ("cpu", "memory")
            },
        }
        nodes.append(node)
        nodes_by_name[node["name"]] = node
    image_events: dict[tuple[str, str], list[dict]] = {}
    for item in resources.get("events", {}).get("items", []):
        involved = item.get("involvedObject", {})
        if involved.get("kind") != "Pod" or item.get("reason") not in {
            "Pulling",
            "Pulled",
        }:
            continue
        key = (involved.get("namespace", "default"), involved.get("name"))
        image_events.setdefault(key, []).append(
            {
                "reason": item.get("reason"),
                "at": item.get("eventTime")
                or item.get("lastTimestamp")
                or item.get("metadata", {}).get("creationTimestamp"),
                "message": item.get("message"),
            }
        )
    pods = []
    for item in resources.get("pods", {}).get("items", []):
        metadata = item.get("metadata", {})
        namespace = metadata.get("namespace", "default")
        name = metadata.get("name")
        status = item.get("status", {})
        container_statuses = status.get("containerStatuses") or []
        termination_reasons = [
            termination.get("reason")
            for container in container_statuses
            if isinstance(container, dict)
            for state_key in ("state", "lastState")
            for termination in [
                (container.get(state_key) or {}).get("terminated") or {}
            ]
            if isinstance(termination, dict) and termination.get("reason")
        ]
        node_name = item.get("spec", {}).get("nodeName")
        scheduled = next(
            (
                condition
                for condition in status.get("conditions") or []
                if condition.get("type") == "PodScheduled"
            ),
            {},
        )
        labels = metadata.get("labels", {})
        identities = {
            name,
            *[value for value in labels.values() if isinstance(value, str)],
        }
        pods.append(
            {
                "namespace": namespace,
                "name": name,
                "identities": sorted(identity for identity in identities if identity),
                "profile": (nodes_by_name.get(node_name) or {}).get("profile"),
                "node": node_name,
                "created_at": metadata.get("creationTimestamp"),
                "scheduled_at": scheduled.get("lastTransitionTime")
                if scheduled.get("status") == "True"
                else None,
                "started_at": status.get("startTime"),
                "phase": status.get("phase"),
                "reason": status.get("reason"),
                "restart_count": sum(
                    int(entry.get("restartCount", 0))
                    for entry in container_statuses
                    if isinstance(entry, dict)
                ),
                "termination_reasons": termination_reasons,
                "usage": pod_metrics.get(f"{namespace}/{name}"),
                "images": [
                    entry.get("image")
                    for entry in container_statuses
                    if isinstance(entry, dict)
                ],
                "image_ids": [
                    entry.get("imageID")
                    for entry in container_statuses
                    if isinstance(entry, dict)
                ],
                "image_pull_events": sorted(
                    image_events.get((namespace, name), []),
                    key=lambda event: event.get("at") or "",
                ),
            }
        )
    runner_sets = []
    for item in resources.get("runner_sets", {}).get("items", []):
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        runner_sets.append(
            {
                "namespace": metadata.get("namespace"),
                "name": metadata.get("name"),
                "desired": status.get("desiredRunners", status.get("desiredReplicas")),
                "current": status.get("currentRunners", status.get("currentReplicas")),
                "pending": status.get("pendingRunners"),
                "running": status.get("runningRunners"),
            }
        )
    quota_names = {"cores", "standardDADSv5Family", "standardFSv2Family"}
    quotas = [
        {
            "name": item.get("name", {}).get("value"),
            "used": int(item.get("currentValue", 0)),
            "limit": int(item.get("limit", 0)),
        }
        for item in resources.get("azure_quotas", [])
        if item.get("name", {}).get("value") in quota_names
    ]
    return {"nodes": nodes, "pods": pods, "runner_sets": runner_sets, "quotas": quotas}


def load_pod_watch(path: Path) -> dict:
    digest = hashlib.sha256()
    latest: dict[tuple[str, str], dict] = {}
    event_count = 0
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(
                    f"invalid pod watch JSON on line {line_number}"
                ) from error
            pod = event.get("pod") if isinstance(event, dict) else None
            namespace = pod.get("namespace") if isinstance(pod, dict) else None
            name = pod.get("name") if isinstance(pod, dict) else None
            if not isinstance(namespace, str) or not namespace:
                raise ValueError(
                    f"pod watch event on line {line_number} lacks a namespace"
                )
            if not isinstance(name, str) or not name:
                raise ValueError(f"pod watch event on line {line_number} lacks a name")
            latest[(namespace, name)] = pod
            event_count += 1

    pods = []
    for (namespace, name), pod in sorted(latest.items()):
        conditions = pod.get("conditions") or []
        container_statuses = pod.get("container_statuses") or []
        if not isinstance(conditions, list) or not isinstance(container_statuses, list):
            raise TypeError(f"invalid pod watch state for {namespace}/{name}")
        scheduled = next(
            (
                condition
                for condition in conditions
                if isinstance(condition, dict)
                and condition.get("type") == "PodScheduled"
                and condition.get("status") == "True"
            ),
            {},
        )
        scale_set = pod.get("scale_set")
        identities = {name}
        if isinstance(scale_set, str) and scale_set:
            identities.add(scale_set)
        termination_reasons = [
            termination.get("reason")
            for container in container_statuses
            if isinstance(container, dict)
            for state_key in ("state", "lastState")
            for termination in [
                (container.get(state_key) or {}).get("terminated") or {}
            ]
            if isinstance(termination, dict) and termination.get("reason")
        ]
        pods.append(
            {
                "namespace": namespace,
                "name": name,
                "identities": sorted(identities),
                "profile": managed_profile([scale_set])
                if isinstance(scale_set, str)
                else None,
                "node": pod.get("node"),
                "created_at": pod.get("created_at"),
                "scheduled_at": scheduled.get("lastTransitionTime"),
                "started_at": pod.get("started_at"),
                "phase": pod.get("phase"),
                "reason": pod.get("reason"),
                "restart_count": sum(
                    int(entry.get("restartCount", 0))
                    for entry in container_statuses
                    if isinstance(entry, dict)
                ),
                "termination_reasons": termination_reasons,
                "usage": None,
                "images": [
                    status.get("image")
                    for status in container_statuses
                    if isinstance(status, dict)
                ],
                "image_ids": [
                    status.get("imageID")
                    for status in container_statuses
                    if isinstance(status, dict)
                ],
                "image_pull_events": [],
                "observed_deleted_at": pod.get("deleted_at"),
            }
        )
    return {
        "path": str(path),
        "sha256": f"sha256:{digest.hexdigest()}",
        "event_count": event_count,
        "pod_count": len(pods),
        "pods": pods,
    }


def merge_observed_pods(summary: dict, observed_pods: list[dict]) -> None:
    current = {
        (pod.get("namespace"), pod.get("name")) for pod in summary.get("pods", [])
    }
    summary.setdefault("pods", []).extend(
        pod
        for pod in observed_pods
        if (pod.get("namespace"), pod.get("name")) not in current
    )
    summary["pods"].sort(
        key=lambda pod: (pod.get("namespace") or "", pod.get("name") or "")
    )


def summarize_pod_stability(pods: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for pod in pods:
        namespace = str(pod.get("namespace") or "")
        profile = pod.get("profile")
        if not namespace.startswith("arc-runners-") or not profile:
            continue
        groups.setdefault(str(profile), []).append(pod)
    return [
        {
            "runner_profile": profile,
            "pods": len(values),
            "failed_pods": sum(pod.get("phase") == "Failed" for pod in values),
            "evictions": sum(pod.get("reason") == "Evicted" for pod in values),
            "container_restarts": sum(
                int(pod.get("restart_count") or 0) for pod in values
            ),
            "oom_kills": sum(
                reason == "OOMKilled"
                for pod in values
                for reason in pod.get("termination_reasons") or []
            ),
            "stable": all(pod.get("phase") != "Failed" for pod in values)
            and all(pod.get("reason") != "Evicted" for pod in values)
            and all(int(pod.get("restart_count") or 0) == 0 for pod in values)
            and all(
                reason != "OOMKilled"
                for pod in values
                for reason in pod.get("termination_reasons") or []
            ),
        }
        for profile, values in sorted(groups.items())
    ]


def load_node_watch(path: Path) -> dict:
    digest = hashlib.sha256()
    latest: dict[str, tuple[str, str | None, dict]] = {}
    event_count = 0
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(
                    f"invalid node watch JSON on line {line_number}"
                ) from error
            node = event.get("node") if isinstance(event, dict) else None
            name = node.get("name") if isinstance(node, dict) else None
            event_type = event.get("event_type") if isinstance(event, dict) else None
            observed_at = event.get("observed_at") if isinstance(event, dict) else None
            if not isinstance(name, str) or not name:
                raise ValueError(f"node watch event on line {line_number} lacks a name")
            if not isinstance(event_type, str) or not event_type:
                raise ValueError(
                    f"node watch event on line {line_number} lacks an event type"
                )
            if observed_at is not None and not isinstance(observed_at, str):
                raise TypeError(
                    f"node watch event on line {line_number} has an invalid timestamp"
                )
            latest[name] = (event_type, observed_at, node)
            event_count += 1

    nodes = []
    for name, (event_type, observed_at, node) in sorted(latest.items()):
        conditions = node.get("conditions") or []
        if not isinstance(conditions, list):
            raise TypeError(f"invalid node watch state for {name}")
        ready = next(
            (
                condition
                for condition in conditions
                if isinstance(condition, dict) and condition.get("type") == "Ready"
            ),
            {},
        )
        nodes.append(
            {
                "name": name,
                "profile": node.get("profile"),
                "created_at": node.get("created_at"),
                "ready_at": ready.get("lastTransitionTime")
                if ready.get("status") == "True"
                else None,
                "removed_at": observed_at if event_type == "DELETED" else None,
                "schedulable": not node.get("unschedulable", False)
                and ready.get("status") == "True",
                "usage": None,
                "allocatable": node.get("allocatable") or {},
            }
        )
    return {
        "path": str(path),
        "sha256": f"sha256:{digest.hexdigest()}",
        "event_count": event_count,
        "node_count": len(nodes),
        "nodes": nodes,
    }


def merge_observed_nodes(summary: dict, observed_nodes: list[dict]) -> None:
    current = {node.get("name") for node in summary.get("nodes", [])}
    summary.setdefault("nodes", []).extend(
        node for node in observed_nodes if node.get("name") not in current
    )
    summary["nodes"].sort(key=lambda node: node.get("name") or "")


def managed_profile(labels: list[str]) -> str | None:
    for profile in (
        "compute-bun-candidate",
        "compute-f32-candidate",
        "compute",
        "container-build",
        "socketless",
    ):
        if any(label == profile or label.endswith(f"-{profile}") for label in labels):
            return profile
    return None


def node_profile_for_runner(profile: str) -> str:
    return {
        "compute-bun-candidate": "compute",
        "compute-f32-candidate": "compute-f32",
    }.get(profile, profile)


def correlate_jobs(jobs: list[dict], summary: dict) -> list[dict]:
    pods = summary["pods"]
    nodes = summary["nodes"]
    samples = []
    for job in jobs:
        runner_name = job.get("runner_name")
        pod = next(
            (
                candidate
                for candidate in pods
                if runner_name and runner_name in candidate["identities"]
            ),
            None,
        )
        profile = managed_profile(job.get("labels", [])) or (
            pod.get("profile") if pod else None
        )
        pod_created = parse_time(pod.get("created_at")) if pod else None
        queued = parse_time(job.get("queued_at"))
        demanded = queued or pod_created
        warm = (
            classify_warm(
                demanded,
                nodes,
                node_profile_for_runner(profile),
                pod.get("node"),
            )
            if demanded and profile and pod
            else None
        )
        scheduled = parse_time(pod.get("scheduled_at")) if pod else None
        started = parse_time(job.get("started_at"))
        arc_assignment_seconds = (
            (started - pod_created).total_seconds() if started and pod_created else None
        )
        sample = dict(job)
        sample.update(
            {
                "profile": profile,
                "warm": warm,
                "pod": pod,
                "github_queue_seconds": job.get("assignment_seconds"),
                "assignment_seconds": arc_assignment_seconds,
                "assignment_slo_eligible": bool(profile)
                and arc_assignment_seconds is not None,
                "pod_schedule_seconds": (scheduled - pod_created).total_seconds()
                if pod_created and scheduled
                else None,
            }
        )
        samples.append(sample)
    return samples


def github_jobs(
    repository: str,
    since: datetime,
    max_runs: int,
    run_ids: list[int] | None = None,
) -> list[dict]:
    if run_ids:
        runs = [
            command_json(["gh", "api", f"repos/{repository}/actions/runs/{run_id}"])
            for run_id in dict.fromkeys(run_ids)
        ]
    else:
        created = since.date().isoformat()
        run_pages = command_json(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/actions/runs?created=>={created}&per_page=100",
            ]
        )
        runs = [run for page in run_pages for run in page["workflow_runs"]][:max_runs]
    jobs = []
    for run in runs:
        run_created = parse_time(run.get("created_at"))
        job_pages = command_json(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/actions/runs/{run['id']}/jobs?filter=all&per_page=100",
            ]
        )
        for job in (job for page in job_pages for job in page["jobs"]):
            queued = parse_time(job.get("created_at"))
            runner_name = job.get("runner_name")
            started = parse_time(job.get("started_at")) if runner_name else None
            completed = parse_time(job.get("completed_at"))
            labels = job.get("labels", [])
            profile = managed_profile(labels)
            steps = []
            for step in job.get("steps", []):
                step_started = parse_time(step.get("started_at"))
                step_completed = parse_time(step.get("completed_at"))
                steps.append(
                    {
                        **step,
                        "duration_seconds": (
                            step_completed - step_started
                        ).total_seconds()
                        if step_started and step_completed
                        else None,
                    }
                )
            jobs.append(
                {
                    "repository": repository,
                    "run_id": run["id"],
                    "run_attempt": run.get("run_attempt"),
                    "run_created_at": run.get("created_at"),
                    "job_id": job["id"],
                    "name": job["name"],
                    "status": job.get("status"),
                    "labels": labels,
                    "runner_name": runner_name,
                    "runner_group_name": job.get("runner_group_name"),
                    "queued_at": job.get("created_at"),
                    "started_at": job.get("started_at") if started else None,
                    "completed_at": job.get("completed_at"),
                    "dependency_wait_seconds": (queued - run_created).total_seconds()
                    if queued and run_created
                    else None,
                    "assignment_seconds": (started - queued).total_seconds()
                    if queued and started
                    else None,
                    "queue_seconds": (started - queued).total_seconds()
                    if queued and started
                    else None,
                    "duration_seconds": (completed - started).total_seconds()
                    if started and completed
                    else None,
                    "conclusion": job.get("conclusion"),
                    "assignment_slo_eligible": profile is not None,
                    "steps": steps,
                    "cache_steps": [
                        step
                        for step in steps
                        if "cache" in step.get("name", "").lower()
                    ],
                }
            )
    return jobs


def classify_step_timing(name: str) -> str:
    normalized = name.strip().lower()
    if normalized in {"set up job", "prepare all required actions"}:
        return "action_preparation"
    if "checkout" in normalized:
        return "checkout"
    if "pull" in normalized and any(
        token in normalized for token in ("image", "docker", "super-linter")
    ):
        return "image_pull"
    if "super-linter" in normalized or "super linter" in normalized:
        return "super_linter"
    if "spectral" in normalized:
        return "spectral"
    if "discover" in normalized and "file" in normalized:
        return "file_discovery"
    if normalized.startswith("post ") or any(
        token in normalized
        for token in ("upload artifact", "finalize profile", "post-processing")
    ):
        return "post_processing"
    if any(
        token in normalized
        for token in (
            "pii",
            "repository hygiene",
            "hardcoded locale",
            "biome",
            "mdx",
            "markdown",
            "native precheck",
        )
    ):
        return "native_prechecks"
    return "other"


def aggregate_job_timings(jobs: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], list[float]] = {}
    for job in jobs:
        identity = (str(job.get("repository")), str(job.get("name")))
        for phase, field in (
            ("dependency_wait", "dependency_wait_seconds"),
            ("runner_assignment", "assignment_seconds"),
            ("total_job", "duration_seconds"),
        ):
            value = job.get(field)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ):
                grouped.setdefault((*identity, phase), []).append(float(value))
        for step in job.get("steps", []):
            value = step.get("duration_seconds")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ):
                phase = classify_step_timing(str(step.get("name", "")))
                grouped.setdefault((*identity, phase), []).append(float(value))
    return [
        {
            "repository": key[0],
            "job": key[1],
            "phase": key[2],
            "runs": len(values),
            "median_seconds": median(values),
            "p95_seconds": percentile95(values),
        }
        for key, values in sorted(grouped.items())
    ]


def burst_job_groups(jobs: list[dict]) -> dict[tuple[str, int, str], list[dict]]:
    groups: dict[tuple[str, int, str], list[dict]] = {}
    for job in jobs:
        name = str(job.get("name", ""))
        variant = next(
            (
                candidate
                for candidate, pattern in BURST_PATTERNS.items()
                if pattern.fullmatch(name)
            ),
            None,
        )
        run_id = job.get("run_id")
        repository = job.get("repository")
        if variant is None or not isinstance(run_id, int) or not repository:
            continue
        groups.setdefault((str(repository), run_id, variant), []).append(job)
    return groups


def burst_clearance_comparisons(jobs: list[dict]) -> list[dict]:
    groups = burst_job_groups(jobs)

    reports = []
    run_keys = sorted({(repository, run_id) for repository, run_id, _ in groups})
    for repository, run_id in run_keys:
        baseline = groups.get((repository, run_id, "baseline"), [])
        baseline_queued = [parse_time(job.get("queued_at")) for job in baseline]
        baseline_completed = [parse_time(job.get("completed_at")) for job in baseline]
        baseline_durations = [
            float(job["duration_seconds"])
            for job in baseline
            if isinstance(job.get("duration_seconds"), (int, float))
            and not isinstance(job.get("duration_seconds"), bool)
        ]
        baseline_clearance = (
            (max(baseline_completed) - min(baseline_queued)).total_seconds()
            if len(baseline) == 4 and all(baseline_queued) and all(baseline_completed)
            else None
        )
        baseline_success = len(baseline) == 4 and all(
            job.get("conclusion") == "success" for job in baseline
        )
        baseline_p95 = (
            percentile95(baseline_durations)
            if len(baseline_durations) == len(baseline) == 4
            else None
        )
        for variant in ("d16-four", "f32"):
            candidate = groups.get((repository, run_id, variant), [])
            candidate_queued = [parse_time(job.get("queued_at")) for job in candidate]
            candidate_completed = [
                parse_time(job.get("completed_at")) for job in candidate
            ]
            candidate_durations = [
                float(job["duration_seconds"])
                for job in candidate
                if isinstance(job.get("duration_seconds"), (int, float))
                and not isinstance(job.get("duration_seconds"), bool)
            ]
            candidate_clearance = (
                (max(candidate_completed) - min(candidate_queued)).total_seconds()
                if len(candidate) == 4
                and all(candidate_queued)
                and all(candidate_completed)
                else None
            )
            candidate_success = len(candidate) == 4 and all(
                job.get("conclusion") == "success" for job in candidate
            )
            candidate_p95 = (
                percentile95(candidate_durations)
                if len(candidate_durations) == len(candidate) == 4
                else None
            )
            improvement = (
                (baseline_clearance - candidate_clearance) / baseline_clearance
                if baseline_clearance and candidate_clearance is not None
                else None
            )
            no_runtime_regression = (
                baseline_p95 is not None
                and candidate_p95 is not None
                and candidate_p95 <= baseline_p95
            )
            reports.append(
                {
                    "repository": repository,
                    "run_id": run_id,
                    "variant": variant,
                    "baseline_jobs": len(baseline),
                    "candidate_jobs": len(candidate),
                    "baseline_clearance_seconds": baseline_clearance,
                    "candidate_clearance_seconds": candidate_clearance,
                    "clearance_improvement_ratio": improvement,
                    "minimum_clearance_improvement_ratio": 0.2,
                    "baseline_p95_runtime_seconds": baseline_p95,
                    "candidate_p95_runtime_seconds": candidate_p95,
                    "no_p95_runtime_regression": no_runtime_regression,
                    "stable": baseline_success and candidate_success,
                    "qualifies": baseline_success
                    and candidate_success
                    and improvement is not None
                    and improvement >= 0.2
                    and no_runtime_regression,
                }
            )
    return reports


def load_price_evidence(path: Path) -> dict:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("price evidence must be a JSON object")
    currency = payload.get("currency")
    region = payload.get("region")
    if not isinstance(currency, str) or not currency:
        raise TypeError("price evidence currency must be a nonempty string")
    if region != "canadacentral":
        raise ValueError("price evidence must be for Canada Central")

    rates = {}
    expected_skus = {
        "d16": "Standard_D16ads_v5",
        "f32": "Standard_F32s_v2",
    }
    for key, expected_sku in expected_skus.items():
        item = payload.get(key)
        if not isinstance(item, dict) or item.get("armSkuName") != expected_sku:
            raise ValueError(f"price evidence lacks exact {expected_sku} pricing")
        if (
            item.get("currencyCode") != currency
            or item.get("unitOfMeasure") != "1 Hour"
        ):
            raise ValueError(f"price evidence has incompatible {expected_sku} units")
        rate = item.get("unitPrice")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
            raise TypeError(f"price evidence has invalid {expected_sku} hourly rate")
        rates[key] = float(rate)

    current_ceiling = rates["d16"] * BURST_COST_MODEL["baseline"]["peak_nodes"]
    maximum_ceiling = current_ceiling * 2
    peak_hourly_costs = {
        variant: rates[model["sku"]] * model["peak_nodes"]
        for variant, model in BURST_COST_MODEL.items()
    }
    return {
        "path": str(path),
        "sha256": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "collected_at": payload.get("collected_at"),
        "source": payload.get("source"),
        "region": region,
        "currency": currency,
        "vm_hourly_rates": rates,
        "runner_slot_hourly_rates": {
            variant: rates[model["sku"]] / model["runners_per_node"]
            for variant, model in BURST_COST_MODEL.items()
        },
        "current_peak_hourly_cost": current_ceiling,
        "maximum_peak_hourly_cost": maximum_ceiling,
        "candidate_peak_hourly_costs": {
            key: peak_hourly_costs[key] for key in ("d16-four", "f32")
        },
        "candidate_peak_below_limit": {
            key: peak_hourly_costs[key] < maximum_ceiling for key in ("d16-four", "f32")
        },
    }


def _runner_cost(jobs: list[dict], slot_hourly_rate: float) -> dict:
    durations = [
        float(job["duration_seconds"])
        for job in jobs
        if isinstance(job.get("duration_seconds"), (int, float))
        and not isinstance(job.get("duration_seconds"), bool)
        and job["duration_seconds"] >= 0
    ]
    successes = sum(job.get("conclusion") == "success" for job in jobs)
    complete = len(jobs) == len(durations) == 4
    total_cost = sum(durations) * slot_hourly_rate / 3600 if complete else None
    return {
        "jobs": len(jobs),
        "successful_jobs": successes,
        "runner_slot_hours": sum(durations) / 3600 if complete else None,
        "total_runner_cost": total_cost,
        "cost_per_successful_workflow": total_cost / successes
        if total_cost is not None and successes
        else None,
        "stable": complete and successes == 4,
    }


def burst_cost_comparisons(jobs: list[dict], pricing: dict | None) -> list[dict]:
    if not pricing:
        return []
    groups = burst_job_groups(jobs)
    rates = pricing["runner_slot_hourly_rates"]
    peak_costs = pricing["candidate_peak_hourly_costs"]
    peak_gate = pricing["candidate_peak_below_limit"]
    reports = []
    run_keys = sorted({(repository, run_id) for repository, run_id, _ in groups})
    for repository, run_id in run_keys:
        baseline = _runner_cost(
            groups.get((repository, run_id, "baseline"), []), rates["baseline"]
        )
        for variant in ("d16-four", "f32"):
            candidate = _runner_cost(
                groups.get((repository, run_id, variant), []), rates[variant]
            )
            baseline_cost = baseline["cost_per_successful_workflow"]
            candidate_cost = candidate["cost_per_successful_workflow"]
            cost_not_increased = (
                baseline_cost is not None
                and candidate_cost is not None
                and candidate_cost <= baseline_cost
            )
            reports.append(
                {
                    "repository": repository,
                    "run_id": run_id,
                    "variant": variant,
                    "currency": pricing["currency"],
                    "baseline": baseline,
                    "candidate": candidate,
                    "cost_not_increased": cost_not_increased,
                    "candidate_peak_hourly_cost": peak_costs[variant],
                    "maximum_peak_hourly_cost": pricing["maximum_peak_hourly_cost"],
                    "peak_below_twice_current": peak_gate[variant],
                    "qualifies": baseline["stable"]
                    and candidate["stable"]
                    and cost_not_increased
                    and peak_gate[variant],
                }
            )
    return reports


def github_workload_profiles(
    repository: str,
    since: datetime,
    max_artifacts: int = 200,
    run_ids: list[int] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    if run_ids:
        pages = []
        for run_id in dict.fromkeys(run_ids):
            pages.extend(
                command_json(
                    [
                        "gh",
                        "api",
                        "--paginate",
                        "--slurp",
                        f"repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
                    ]
                )
            )
    else:
        pages = command_json(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/actions/artifacts?per_page=100",
            ]
        )
    artifacts = [artifact for page in pages for artifact in page.get("artifacts", [])]
    selected_run_ids = {str(run_id) for run_id in run_ids or []}
    profiles, filesystem_reports, rejected = [], [], []
    for artifact in artifacts:
        if len(profiles) >= max_artifacts:
            break
        created = parse_time(artifact.get("created_at"))
        if (
            not str(artifact.get("name", "")).startswith("workload-profile-")
            or artifact.get("expired")
            or not created
            or (not selected_run_ids and created < since)
        ):
            continue
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact['id']}/zip"],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            rejected.append(
                {"artifact_id": artifact["id"], "reason": "download_failed"}
            )
            continue
        try:
            artifact_profiles = []
            artifact_filesystem = None
            with zipfile.ZipFile(io.BytesIO(result.stdout)) as archive:
                for name in archive.namelist():
                    member = PurePosixPath(name)
                    if member == PurePosixPath("node-filesystem.json"):
                        artifact_filesystem = validate_node_filesystem(
                            json.loads(archive.read(name))
                        )
                        continue
                    if member.suffix != ".json" or not (
                        member.name == "profile.json" or "profiles" in member.parts
                    ):
                        continue
                    profile = validate_workload_profile(json.loads(archive.read(name)))
                    if (
                        not selected_run_ids
                        or str(profile.get("run_id")) in selected_run_ids
                    ):
                        artifact_profiles.append(profile)
            if not artifact_profiles:
                raise ValueError("artifact contains no selected workload profiles")
            if artifact_filesystem:
                identity = artifact_profiles[0]
                filesystem_reports.append(
                    {
                        "artifact_id": artifact["id"],
                        "artifact_name": artifact.get("name"),
                        "repository": identity.get("repository"),
                        "run_id": identity.get("run_id"),
                        "job_id": identity.get("job_id"),
                        "runner_name": identity.get("runner_name"),
                        "runner_profile": identity.get("runner_profile"),
                        "variant": identity.get("variant"),
                        "pair_id": identity.get("pair_id"),
                        "cache_state": identity.get("cache_state"),
                        **artifact_filesystem,
                        "disk_below_70_percent": artifact_filesystem["used_ratio"]
                        < 0.7,
                    }
                )
            profiles.extend(artifact_profiles)
        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            zipfile.BadZipFile,
        ):
            rejected.append(
                {"artifact_id": artifact["id"], "reason": "invalid_profile"}
            )
    return profiles, filesystem_reports, rejected


def aggregate_node_filesystems(reports: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for report in reports:
        key = (
            report.get("repository"),
            report.get("variant"),
            report.get("cache_state"),
            report.get("runner_profile"),
        )
        groups.setdefault(key, []).append(report)
    return [
        {
            "repository": key[0],
            "variant": key[1],
            "cache_state": key[2],
            "runner_profile": key[3],
            "runs": len(values),
            "max_used_ratio": max(value["used_ratio"] for value in values),
            "disk_below_70_percent": all(
                value["disk_below_70_percent"] for value in values
            ),
        }
        for key, values in sorted(
            groups.items(), key=lambda item: tuple(str(part) for part in item[0])
        )
    ]


def cpu_throttling_ratio(profile: dict) -> float | None:
    cpu = profile.get("cpu")
    if not isinstance(cpu, dict):
        return None
    periods = cpu.get("nr_periods")
    throttled = cpu.get("nr_throttled")
    if (
        isinstance(periods, bool)
        or not isinstance(periods, int)
        or periods < 0
        or isinstance(throttled, bool)
        or not isinstance(throttled, int)
        or throttled < 0
        or throttled > periods
    ):
        return None
    return throttled / periods if periods else 0.0


def aggregate_workload_profiles(profiles: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for profile in profiles:
        key = (
            profile.get("repository"),
            profile.get("phase"),
            profile.get("variant"),
            profile.get("cache_state"),
            profile.get("runner_profile"),
        )
        groups.setdefault(key, []).append(profile)
    reports = []
    for key, values in sorted(
        groups.items(), key=lambda item: tuple(str(part) for part in item[0])
    ):
        durations = [float(value["duration_seconds"]) for value in values]
        memory = [value.get("memory", {}).get("peak_limit_ratio") for value in values]
        throttle_ratios = [
            ratio
            for value in values
            if (ratio := cpu_throttling_ratio(value)) is not None
        ]
        docker_values = [
            value for value in values if value.get("profile_kind") == "docker_action"
        ]
        reports.append(
            {
                "repository": key[0],
                "phase": key[1],
                "variant": key[2],
                "cache_state": key[3],
                "runner_profile": key[4],
                "runs": len(values),
                "median_seconds": median(durations),
                "p95_seconds": percentile95(durations),
                "max_peak_memory_ratio": max(
                    (value for value in memory if value is not None), default=None
                ),
                "median_cpu_throttle_ratio": median(throttle_ratios)
                if throttle_ratios
                else None,
                "p95_cpu_throttle_ratio": percentile95(throttle_ratios),
                "failures": sum(
                    (
                        value.get("observer", {}).get("result") != "completed"
                        or value.get("exit", {}).get("code") != 0
                    )
                    if value.get("profile_kind") == "docker_action"
                    else value.get("exit", {}).get("code") != 0
                    for value in values
                ),
                "oom_events": sum(
                    int(value.get("memory", {}).get("oom", False))
                    if value.get("profile_kind") == "docker_action"
                    else value.get("memory", {}).get("events", {}).get("oom_kill", 0)
                    for value in values
                ),
                "median_image_bytes": median(
                    [
                        value["image"]["size_bytes"]
                        for value in docker_values
                        if value["image"]["size_bytes"] is not None
                    ]
                )
                if any(
                    value["image"]["size_bytes"] is not None for value in docker_values
                )
                else None,
                "median_cpu_seconds": median(
                    [value["cpu"]["usage_seconds"] for value in docker_values]
                )
                if docker_values
                else None,
                "max_pids": max(
                    (value["pids"]["peak"] for value in docker_values), default=None
                ),
                "block_read_bytes": sum(
                    value["block_io"]["read_bytes"] for value in docker_values
                ),
                "block_write_bytes": sum(
                    value["block_io"]["write_bytes"] for value in docker_values
                ),
                "network_receive_bytes": sum(
                    value["network_io"]["receive_bytes"] for value in docker_values
                ),
                "network_transmit_bytes": sum(
                    value["network_io"]["transmit_bytes"] for value in docker_values
                ),
            }
        )
    return reports


def performance_comparisons(profiles: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for profile in profiles:
        if profile.get("profile_kind") == "docker_action":
            continue
        key = (
            profile.get("repository"),
            profile.get("phase"),
            profile.get("cache_state"),
        )
        groups.setdefault(key, []).append(profile)
    results = []
    for key, values in groups.items():
        baseline = {
            item.get("pair_id"): item
            for item in values
            if item.get("variant") == "baseline" and item.get("pair_id")
        }
        variants = sorted(
            {
                item.get("variant")
                for item in values
                if item.get("variant") not in (None, "baseline")
            }
        )
        for variant in variants:
            minimum_improvement = 0.0 if variant == "bun-1.4.2" else 0.2
            required_pairs = 4 if str(key[1]).endswith("-burst") else 5
            candidate = {
                item.get("pair_id"): item
                for item in values
                if item.get("variant") == variant and item.get("pair_id")
            }
            pairs = sorted(set(baseline) & set(candidate))
            base_values = [baseline[pair]["duration_seconds"] for pair in pairs]
            candidate_values = [candidate[pair]["duration_seconds"] for pair in pairs]
            base_median = median(base_values) if base_values else None
            candidate_median = median(candidate_values) if candidate_values else None
            improvement = (
                (base_median - candidate_median) / base_median if base_median else None
            )
            correct = bool(pairs) and all(
                baseline[pair].get("output_digest") is not None
                and baseline[pair].get("output_digest")
                == candidate[pair].get("output_digest")
                for pair in pairs
            )
            stable = all(
                item.get("exit", {}).get("code") == 0
                and item.get("memory", {}).get("events", {}).get("oom_kill", 0) == 0
                for pair in pairs
                for item in (baseline[pair], candidate[pair])
            )
            memory_ratios = [
                candidate[pair]["memory"]["peak_limit_ratio"] for pair in pairs
            ]
            memory_ok = bool(memory_ratios) and all(
                ratio is not None and ratio < 0.8 for ratio in memory_ratios
            )
            base_throttle = [cpu_throttling_ratio(baseline[pair]) for pair in pairs]
            candidate_throttle = [
                cpu_throttling_ratio(candidate[pair]) for pair in pairs
            ]
            throttle_evidence_complete = bool(pairs) and all(
                ratio is not None for ratio in (*base_throttle, *candidate_throttle)
            )
            base_throttle_values = [
                float(ratio) for ratio in base_throttle if ratio is not None
            ]
            candidate_throttle_values = [
                float(ratio) for ratio in candidate_throttle if ratio is not None
            ]
            base_throttle_median = (
                median(base_throttle_values) if base_throttle_values else None
            )
            candidate_throttle_median = (
                median(candidate_throttle_values) if candidate_throttle_values else None
            )
            no_sustained_throttling_regression = (
                throttle_evidence_complete
                and base_throttle_median is not None
                and candidate_throttle_median is not None
                and candidate_throttle_median
                <= base_throttle_median + MAX_SUSTAINED_THROTTLE_REGRESSION_RATIO
            )
            base_p95 = percentile95(base_values)
            candidate_p95 = percentile95(candidate_values)
            qualifies = (
                len(pairs) >= required_pairs
                and improvement is not None
                and improvement >= minimum_improvement
                and candidate_p95 is not None
                and base_p95 is not None
                and candidate_p95 <= base_p95
                and correct
                and stable
                and memory_ok
                and no_sustained_throttling_regression
            )
            results.append(
                {
                    "repository": key[0],
                    "phase": key[1],
                    "cache_state": key[2],
                    "variant": variant,
                    "paired_runs": len(pairs),
                    "required_pairs": required_pairs,
                    "baseline_median_seconds": base_median,
                    "candidate_median_seconds": candidate_median,
                    "median_improvement_ratio": improvement,
                    "minimum_median_improvement_ratio": minimum_improvement,
                    "baseline_p95_seconds": base_p95,
                    "candidate_p95_seconds": candidate_p95,
                    "output_equivalent": correct,
                    "stable": stable,
                    "memory_below_80_percent": memory_ok,
                    "baseline_median_cpu_throttle_ratio": base_throttle_median,
                    "baseline_p95_cpu_throttle_ratio": percentile95(
                        base_throttle_values
                    ),
                    "candidate_median_cpu_throttle_ratio": candidate_throttle_median,
                    "candidate_p95_cpu_throttle_ratio": percentile95(
                        candidate_throttle_values
                    ),
                    "maximum_sustained_throttle_regression_ratio": MAX_SUSTAINED_THROTTLE_REGRESSION_RATIO,
                    "no_sustained_cpu_throttling_regression": no_sustained_throttling_regression,
                    "qualifies": qualifies,
                }
            )
    return sorted(
        results,
        key=lambda item: tuple(
            str(item[key]) for key in ("repository", "phase", "cache_state", "variant")
        ),
    )


def repository_caps(root: Path = ROOT) -> dict[str, dict[str, int]]:
    caps = {}
    for path in sorted((root / "arc/repositories").glob("*.yaml")):
        config = json.loads(path.read_text(encoding="utf-8"))
        repository = config["repository"].removeprefix("https://github.com/")
        caps[repository] = {
            scale_set["profile"]: scale_set["max_runners"]
            for scale_set in config["scale_sets"]
        }
    return caps


def cap_recommendations(samples: list[dict], policy: dict, existing: dict) -> dict:
    interval = policy["sample_minutes"]
    buckets: dict[tuple[str, str, datetime], int] = {}
    for sample in samples:
        repository = sample.get("repository")
        profile = sample.get("profile")
        started = parse_time(sample.get("started_at"))
        completed = parse_time(sample.get("completed_at"))
        if not repository or not profile or not started or not completed:
            continue
        bucket = started.replace(
            minute=(started.minute // interval) * interval, second=0, microsecond=0
        )
        while bucket < completed:
            buckets[(repository, profile, bucket)] = (
                buckets.get((repository, profile, bucket), 0) + 1
            )
            bucket += timedelta(minutes=interval)
    result = {}
    for repository, profiles in existing.items():
        result[repository] = {}
        for profile, current_cap in profiles.items():
            counts = [
                count
                for (sample_repository, sample_profile, _), count in buckets.items()
                if sample_repository == repository and sample_profile == profile
            ]
            peak = max(counts, default=0)
            p95 = percentile95([float(value) for value in counts]) or 0.0
            capacity = policy["pool_capacity"][profile]
            result[repository][profile] = {
                "existing": current_cap,
                "observed_peak": peak,
                "p95_five_minute_concurrency": p95,
                "recommended": recommend_cap(capacity, current_cap, peak, p95),
            }
    return result


def collect(args, policy: dict) -> dict:
    now = datetime.now(UTC)
    since = now - timedelta(days=args.days)
    jobs, profiles, filesystem_reports, rejected = [], [], [], []
    for repository in args.repository:
        repository_jobs = github_jobs(repository, since, args.max_runs, args.run_id)
        cutoff = parse_time(policy.get("baseline_not_before", {}).get(repository))
        if cutoff:
            repository_jobs = [
                job
                for job in repository_jobs
                if (parse_time(job.get("run_created_at")) or since) >= cutoff
            ]
        jobs.extend(repository_jobs)
        found, found_filesystems, invalid = github_workload_profiles(
            repository, since, args.max_artifacts, args.run_id
        )
        profiles.extend(found)
        filesystem_reports.extend(found_filesystems)
        rejected.extend({"repository": repository, **item} for item in invalid)
    kubernetes = kubernetes_snapshot()
    summary = summarize_kubernetes(kubernetes)
    pod_observer = load_pod_watch(args.pod_watch) if args.pod_watch else None
    node_observer = load_node_watch(args.node_watch) if args.node_watch else None
    if node_observer:
        merge_observed_nodes(summary, node_observer["nodes"])
    if pod_observer:
        merge_observed_pods(summary, pod_observer["pods"])
    samples = correlate_jobs(jobs, summary)
    selected_pods = {
        (sample["pod"].get("namespace"), sample["pod"].get("name")): sample["pod"]
        for sample in samples
        if isinstance(sample.get("pod"), dict)
    }
    price_evidence = (
        load_price_evidence(args.price_evidence) if args.price_evidence else None
    )
    return {
        "schema_version": 2,
        "collected_at": now.isoformat(),
        "range": {"start": since.isoformat(), "end": now.isoformat()},
        "selected_run_ids": [str(run_id) for run_id in args.run_id or []],
        "policy": policy,
        "samples": samples,
        "workload_profiles": profiles,
        "rejected_workload_profiles": rejected,
        "job_timing_reports": aggregate_job_timings(jobs),
        "assignment_slo_summary": assignment_slo_summary(samples, policy),
        "burst_clearance_comparisons": burst_clearance_comparisons(jobs),
        "price_evidence": price_evidence,
        "burst_cost_comparisons": burst_cost_comparisons(jobs, price_evidence),
        "workload_reports": aggregate_workload_profiles(profiles),
        "node_filesystem_reports": filesystem_reports,
        "node_filesystem_summary": aggregate_node_filesystems(filesystem_reports),
        "performance_comparisons": performance_comparisons(profiles),
        "repository_cap_recommendations": cap_recommendations(
            samples, policy, repository_caps()
        ),
        "kubernetes": kubernetes,
        "kubernetes_summary": summary,
        "pod_stability_summary": summarize_pod_stability(summary["pods"]),
        "selected_run_pod_stability_summary": summarize_pod_stability(
            list(selected_pods.values())
        ),
        "pod_observer": {
            key: value for key, value in pod_observer.items() if key != "pods"
        }
        if pod_observer
        else None,
        "node_observer": {
            key: value for key, value in node_observer.items() if key != "nodes"
        }
        if node_observer
        else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--repository", action="append", required=True)
    collect_parser.add_argument("--days", type=int, default=30)
    collect_parser.add_argument("--max-runs", type=int, default=200)
    collect_parser.add_argument("--max-artifacts", type=int, default=200)
    collect_parser.add_argument(
        "--pod-watch",
        type=Path,
        help="pod lifecycle JSONL captured while the selected workflow ran",
    )
    collect_parser.add_argument(
        "--node-watch",
        type=Path,
        help="node lifecycle JSONL captured while the selected workflow ran",
    )
    collect_parser.add_argument(
        "--run-id",
        action="append",
        type=int,
        help="collect only this workflow run (repeatable; requires one repository)",
    )
    collect_parser.add_argument(
        "--price-evidence",
        type=Path,
        help="validated Canada Central D16/F32 hourly price evidence",
    )
    collect_parser.add_argument("--output", type=Path)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    if args.command == "collect" and args.run_id and len(args.repository) != 1:
        collect_parser.error("--run-id requires exactly one --repository")
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if args.command == "collect":
        result = collect(args, policy)
        payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
        else:
            sys.stdout.write(payload)
    else:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        result = evaluate(
            evidence["samples"],
            policy,
            datetime.now(UTC),
            evidence.get("kubernetes_summary", {}).get("quotas"),
        )
        for key in (
            "repository_cap_recommendations",
            "job_timing_reports",
            "burst_clearance_comparisons",
            "price_evidence",
            "burst_cost_comparisons",
            "pod_stability_summary",
            "selected_run_pod_stability_summary",
            "workload_reports",
            "node_filesystem_reports",
            "node_filesystem_summary",
            "performance_comparisons",
            "rejected_workload_profiles",
        ):
            default = {} if key == "repository_cap_recommendations" else []
            if key == "price_evidence":
                default = None
            result[key] = evidence.get(key, default)
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
