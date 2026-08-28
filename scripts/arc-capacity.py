#!/usr/bin/env python3
"""Collect and evaluate correlated ARC assignment/capacity evidence."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from statistics import quantiles
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "config/arc-capacity.json"


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
    """Apply the governed cap formula without permitting oversubscription."""
    return min(
        pool_capacity, max(existing, peak + 2, math.ceil(1.25 * p95_five_minute))
    )


def classify_warm(queued_at: datetime, nodes: list[dict], profile: str) -> bool:
    """Warm means a schedulable Ready node of the requested class existed at queue time."""
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
        queued = parse_time(sample.get("queued_at"))
        if queued and sample.get("started_at"):
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
        wait = sample.get("queue_seconds")
        if wait is not None and wait >= policy["alerts"]["job_wait_seconds"]:
            alerts.append(
                {"kind": "job_wait", "job": sample.get("job_id"), "seconds": wait}
            )
        pending = sample.get("pending_at_pool_max_seconds", 0)
        if pending >= policy["alerts"]["pending_at_pool_max_seconds"]:
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
    """Index best-effort kubectl top output without treating missing metrics as zero."""
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
        labels = job.get("labels", [])
        profile = next(
            (
                name
                for name in ("compute", "container-build", "socketless")
                if any(label == name or label.endswith(f"-{name}") for label in labels)
            ),
            pod.get("profile") if pod else None,
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
            jobs.append(
                {
                    "repository": repository,
                    "run_id": run["id"],
                    "job_id": job["id"],
                    "name": job["name"],
                    "labels": job.get("labels", []),
                    "runner_name": job.get("runner_name"),
                    "runner_group_name": job.get("runner_group_name"),
                    "queued_at": job.get("created_at"),
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "queue_seconds": (started - queued).total_seconds()
                    if queued and started
                    else None,
                    "duration_seconds": (
                        parse_time(job.get("completed_at")) - started
                    ).total_seconds()
                    if started and job.get("completed_at")
                    else None,
                    "conclusion": job.get("conclusion"),
                    "cache_steps": [
                        step
                        for step in job.get("steps", [])
                        if "cache" in step.get("name", "").lower()
                    ],
                }
            )
    return jobs


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
    """Calculate five-minute concurrency and apply the governed cap formula."""
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
            minute=(started.minute // interval) * interval,
            second=0,
            microsecond=0,
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
    jobs = [
        job
        for repo in args.repository
        for job in github_jobs(repo, since, args.max_runs)
    ]
    kubernetes = kubernetes_snapshot()
    summary = summarize_kubernetes(kubernetes)
    samples = correlate_jobs(jobs, summary)
    return {
        "schema_version": 1,
        "collected_at": now.isoformat(),
        "range": {"start": since.isoformat(), "end": now.isoformat()},
        "policy": policy,
        "samples": samples,
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
        result["repository_cap_recommendations"] = evidence.get(
            "repository_cap_recommendations", {}
        )
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
