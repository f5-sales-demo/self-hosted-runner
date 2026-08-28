#!/usr/bin/env python3
"""Collect and evaluate correlated ARC assignment and workload evidence."""

from __future__ import annotations

import argparse
import io
import json
import math
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
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


def validate_workload_profile(profile: object) -> dict:
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


def classify_warm(queued_at: datetime, nodes: list[dict], profile: str) -> bool:
    for node in nodes:
        if node.get("profile") != profile or not node.get("schedulable", False):
            continue
        ready = parse_time(node.get("ready_at"))
        removed = parse_time(node.get("removed_at"))
        if ready and ready <= queued_at and (removed is None or queued_at < removed):
            return True
    return False


def in_service_window(instant: datetime, policy: dict) -> bool:
    local = instant.astimezone(ZoneInfo(policy["timezone"]))
    return (
        policy["service_window"]["start_hour"]
        <= local.hour
        < policy["service_window"]["end_hour"]
    )


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
            started = parse_time(sample["started_at"])
            assert started is not None
            boundary = queued.replace(
                minute=(queued.minute // interval) * interval, second=0, microsecond=0
            )
            warm = sample.get("warm")
            if warm is not None:
                kind = "warm" if warm else "cold"
                buckets.setdefault((kind, boundary), []).append(
                    (started - queued).total_seconds()
                )
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
                "usage": pod_metrics.get(f"{namespace}/{name}"),
                "images": [
                    entry.get("image")
                    for entry in status.get("containerStatuses") or []
                ],
                "image_ids": [
                    entry.get("imageID")
                    for entry in status.get("containerStatuses") or []
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
    quota_names = {"cores", "standardDADSv5Family"}
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


def managed_profile(labels: list[str]) -> str | None:
    for profile in ("compute", "container-build", "socketless"):
        if any(label == profile or label.endswith(f"-{profile}") for label in labels):
            return profile
    return None


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
        queued = parse_time(job.get("queued_at"))
        warm = (
            classify_warm(queued, nodes, profile)
            if queued and profile and pod
            else None
        )
        scheduled = parse_time(pod.get("scheduled_at")) if pod else None
        sample = dict(job)
        sample.update(
            {
                "profile": profile,
                "warm": warm,
                "pod": pod,
                "assignment_slo_eligible": bool(profile),
                "pod_schedule_seconds": (scheduled - queued).total_seconds()
                if queued and scheduled
                else None,
            }
        )
        samples.append(sample)
    return samples


def github_jobs(repository: str, since: datetime, max_runs: int) -> list[dict]:
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
            started = parse_time(job.get("started_at"))
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
                    "labels": labels,
                    "runner_name": job.get("runner_name"),
                    "runner_group_name": job.get("runner_group_name"),
                    "queued_at": job.get("created_at"),
                    "started_at": job.get("started_at"),
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


def github_workload_profiles(
    repository: str, since: datetime, max_artifacts: int = 200
) -> tuple[list[dict], list[dict]]:
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
    profiles, rejected = [], []
    for artifact in artifacts:
        if len(profiles) >= max_artifacts:
            break
        created = parse_time(artifact.get("created_at"))
        if (
            not str(artifact.get("name", "")).startswith("workload-profile-")
            or artifact.get("expired")
            or not created
            or created < since
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
            with zipfile.ZipFile(io.BytesIO(result.stdout)) as archive:
                for name in archive.namelist():
                    if not name.endswith(".json"):
                        continue
                    profile = validate_workload_profile(json.loads(archive.read(name)))
                    artifact_profiles.append(profile)
            if not artifact_profiles:
                raise ValueError("artifact contains no workload profiles")
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
    return profiles, rejected


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
                "failures": sum(
                    value.get("exit", {}).get("code") != 0 for value in values
                ),
                "oom_events": sum(
                    value.get("memory", {}).get("events", {}).get("oom_kill", 0)
                    for value in values
                ),
            }
        )
    return reports


def performance_comparisons(profiles: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for profile in profiles:
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
            base_p95 = percentile95(base_values)
            candidate_p95 = percentile95(candidate_values)
            qualifies = (
                len(pairs) >= 5
                and improvement is not None
                and improvement >= 0.2
                and candidate_p95 is not None
                and base_p95 is not None
                and candidate_p95 <= base_p95
                and correct
                and stable
                and memory_ok
            )
            results.append(
                {
                    "repository": key[0],
                    "phase": key[1],
                    "cache_state": key[2],
                    "variant": variant,
                    "paired_runs": len(pairs),
                    "baseline_median_seconds": base_median,
                    "candidate_median_seconds": candidate_median,
                    "median_improvement_ratio": improvement,
                    "baseline_p95_seconds": base_p95,
                    "candidate_p95_seconds": candidate_p95,
                    "output_equivalent": correct,
                    "stable": stable,
                    "memory_below_80_percent": memory_ok,
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
    jobs, profiles, rejected = [], [], []
    for repository in args.repository:
        repository_jobs = github_jobs(repository, since, args.max_runs)
        cutoff = parse_time(policy.get("baseline_not_before", {}).get(repository))
        if cutoff:
            repository_jobs = [
                job
                for job in repository_jobs
                if (parse_time(job.get("run_created_at")) or since) >= cutoff
            ]
        jobs.extend(repository_jobs)
        found, invalid = github_workload_profiles(repository, since, args.max_artifacts)
        profiles.extend(found)
        rejected.extend({"repository": repository, **item} for item in invalid)
    kubernetes = kubernetes_snapshot()
    summary = summarize_kubernetes(kubernetes)
    samples = correlate_jobs(jobs, summary)
    return {
        "schema_version": 2,
        "collected_at": now.isoformat(),
        "range": {"start": since.isoformat(), "end": now.isoformat()},
        "policy": policy,
        "samples": samples,
        "workload_profiles": profiles,
        "rejected_workload_profiles": rejected,
        "workload_reports": aggregate_workload_profiles(profiles),
        "performance_comparisons": performance_comparisons(profiles),
        "repository_cap_recommendations": cap_recommendations(
            samples, policy, repository_caps()
        ),
        "kubernetes": kubernetes,
        "kubernetes_summary": summary,
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
    collect_parser.add_argument("--output", type=Path)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
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
            "workload_reports",
            "performance_comparisons",
            "rejected_workload_profiles",
        ):
            result[key] = evidence.get(
                key, [] if key != "repository_cap_recommendations" else {}
            )
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
