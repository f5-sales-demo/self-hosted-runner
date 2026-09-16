#!/usr/bin/env python3
"""Repair and verify ARC listeners after runner scale-set reconciliation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from typing import Any

NAMESPACE_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")


def kubectl_json(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    document = json.loads(completed.stdout)
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise TypeError("kubectl returned an invalid Kubernetes List")
    return document


def target_listeners(
    resources: dict[str, Any], namespaces: set[str]
) -> list[dict[str, Any]]:
    return [
        item
        for item in resources["items"]
        if item.get("kind") == "AutoscalingListener"
        and item.get("spec", {}).get("autoscalingRunnerSetNamespace") in namespaces
    ]


def stale_listeners(
    resources: dict[str, Any], namespaces: set[str]
) -> list[dict[str, str]]:
    ephemeral_sets = {
        (
            item.get("metadata", {}).get("namespace"),
            item.get("metadata", {}).get("name"),
        )
        for item in resources["items"]
        if item.get("kind") == "EphemeralRunnerSet"
    }
    stale: list[dict[str, str]] = []
    for listener in target_listeners(resources, namespaces):
        spec = listener.get("spec", {})
        reference = (
            spec.get("autoscalingRunnerSetNamespace"),
            spec.get("ephemeralRunnerSetName"),
        )
        if reference in ephemeral_sets:
            continue
        metadata = listener.get("metadata", {})
        stale.append(
            {
                "name": str(metadata.get("name", "")),
                "namespace": str(metadata.get("namespace", "")),
                "uid": str(metadata.get("uid", "")),
                "runner_namespace": str(reference[0] or ""),
                "ephemeral_runner_set": str(reference[1] or ""),
            }
        )
    return stale


def listener_health(
    resources: dict[str, Any], pods: dict[str, Any], namespaces: set[str]
) -> dict[str, list[str]]:
    listeners = target_listeners(resources, namespaces)
    listeners_by_runner_namespace: dict[str, list[dict[str, Any]]] = {}
    for listener in listeners:
        runner_namespace = listener.get("spec", {}).get(
            "autoscalingRunnerSetNamespace", ""
        )
        listeners_by_runner_namespace.setdefault(runner_namespace, []).append(listener)

    missing = sorted(namespaces - listeners_by_runner_namespace.keys())
    duplicate = sorted(
        namespace
        for namespace, values in listeners_by_runner_namespace.items()
        if len(values) != 1
    )
    stale = sorted(
        f"{item['namespace']}/{item['name']}"
        for item in stale_listeners(resources, namespaces)
    )

    pods_by_name = {
        item.get("metadata", {}).get("name"): item for item in pods["items"]
    }
    unhealthy: list[str] = []
    for listener in listeners:
        name = listener.get("metadata", {}).get("name", "")
        pod = pods_by_name.get(name)
        if not pod:
            unhealthy.append(f"{name}:missing-pod")
            continue
        status = pod.get("status", {})
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in status.get("conditions") or []
        )
        restarts = sum(
            int(container.get("restartCount", 0))
            for container in status.get("containerStatuses") or []
        )
        if status.get("phase") != "Running" or not ready or restarts:
            unhealthy.append(
                f"{name}:phase={status.get('phase')},ready={ready},restarts={restarts}"
            )

    return {
        "missing": missing,
        "duplicate": duplicate,
        "stale": stale,
        "unhealthy": sorted(unhealthy),
    }


def reconcile(namespaces: set[str], timeout_seconds: int, poll_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    stable_observations = 0
    deleted_uids: set[str] = set()
    last_health: dict[str, list[str]] = {}

    while time.monotonic() < deadline:
        resources = kubectl_json(
            "get", "autoscalinglisteners,ephemeralrunnersets", "-A"
        )
        for listener in stale_listeners(resources, namespaces):
            if listener["uid"] in deleted_uids:
                continue
            subprocess.run(
                [
                    "kubectl",
                    "delete",
                    "autoscalinglistener",
                    listener["name"],
                    "--namespace",
                    listener["namespace"],
                    "--wait=false",
                ],
                check=True,
            )
            deleted_uids.add(listener["uid"])
            print(
                "reconciled stale ARC listener "
                f"{listener['namespace']}/{listener['name']}"
            )

        resources = kubectl_json(
            "get", "autoscalinglisteners,ephemeralrunnersets", "-A"
        )
        pods = kubectl_json("get", "pods", "--namespace", "arc-systems")
        last_health = listener_health(resources, pods, namespaces)
        if not any(last_health.values()):
            stable_observations += 1
            if stable_observations >= 2:
                print(f"ARC listeners ready for {len(namespaces)} runner namespaces")
                return
        else:
            stable_observations = 0
        time.sleep(poll_seconds)

    raise RuntimeError(
        "ARC listeners did not become stable before timeout: "
        + json.dumps(last_health, sort_keys=True)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("namespaces", nargs="+")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("timeouts must be positive")
    invalid = [value for value in args.namespaces if not NAMESPACE_RE.fullmatch(value)]
    if invalid:
        parser.error("runner namespaces must be valid DNS labels")
    return args


def main() -> None:
    args = parse_args()
    reconcile(set(args.namespaces), args.timeout_seconds, args.poll_seconds)


if __name__ == "__main__":
    main()
