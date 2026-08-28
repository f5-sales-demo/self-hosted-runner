#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "arc_capacity", ROOT / "scripts/arc-capacity.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ArcCapacityTests(unittest.TestCase):
    def test_repository_cap_formula_is_bounded_and_deterministic(self) -> None:
        self.assertEqual(18, MODULE.recommend_cap(30, 10, 16, 12.1))
        self.assertEqual(30, MODULE.recommend_cap(30, 10, 40, 35))
        self.assertEqual(10, MODULE.recommend_cap(30, 10, 4, 6))

    def test_warm_requires_ready_schedulable_node_at_queue_time(self) -> None:
        queued = datetime(2026, 8, 28, 14, tzinfo=UTC)
        nodes = [
            {
                "profile": "compute",
                "schedulable": True,
                "ready_at": "2026-08-28T13:59:00Z",
                "removed_at": None,
            }
        ]
        self.assertTrue(MODULE.classify_warm(queued, nodes, "compute"))
        nodes[0]["ready_at"] = "2026-08-28T14:00:01Z"
        self.assertFalse(MODULE.classify_warm(queued, nodes, "compute"))
        nodes[0]["ready_at"] = "2026-08-28T13:59:00Z"
        nodes[0]["schedulable"] = False
        self.assertFalse(MODULE.classify_warm(queued, nodes, "compute"))

    def test_two_consecutive_service_window_breaches_page(self) -> None:
        policy = {
            "timezone": "America/Toronto",
            "service_window": {"start_hour": 6, "end_hour": 22},
            "sample_minutes": 5,
            "consecutive_slo_breaches": 2,
            "slo_seconds": {"warm_assignment_p95": 20, "cold_assignment_p95": 180},
            "alerts": {
                "pending_at_pool_max_seconds": 120,
                "job_wait_seconds": 600,
                "minimum_quota_headroom_ratio": 0.2,
            },
        }
        samples = [
            {
                "job_id": 1,
                "queued_at": "2026-08-28T14:00:00Z",
                "started_at": "2026-08-28T14:00:30Z",
                "queue_seconds": 30,
                "warm": True,
            },
            {
                "job_id": 2,
                "queued_at": "2026-08-28T14:05:00Z",
                "started_at": "2026-08-28T14:05:25Z",
                "queue_seconds": 25,
                "warm": True,
            },
        ]
        result = MODULE.evaluate(
            samples, policy, datetime(2026, 8, 28, 14, 10, tzinfo=UTC)
        )
        self.assertTrue(result["paging"])
        self.assertEqual("assignment_slo", result["alerts"][0]["kind"])
        self.assertTrue(result["alerts"][0]["page"])

    def test_kubernetes_summary_correlates_runtime_evidence(self) -> None:
        resources = {
            "node_metrics": ["node-a 250m 2% 4Gi 6%"],
            "pod_metrics": ["arc-runners-xcsh-compute runner-a 500m 2Gi"],
            "nodes": {
                "items": [
                    {
                        "metadata": {
                            "name": "node-a",
                            "creationTimestamp": "2026-08-28T13:55:00Z",
                            "labels": {"runner-profile": "compute"},
                        },
                        "spec": {},
                        "status": {
                            "allocatable": {"cpu": "16", "memory": "64Gi"},
                            "conditions": [
                                {
                                    "type": "Ready",
                                    "status": "True",
                                    "lastTransitionTime": "2026-08-28T13:59:00Z",
                                }
                            ],
                        },
                    }
                ]
            },
            "pods": {
                "items": [
                    {
                        "metadata": {
                            "name": "runner-a",
                            "namespace": "arc-runners-xcsh-compute",
                            "creationTimestamp": "2026-08-28T14:00:01Z",
                            "labels": {"runner-profile": "compute"},
                        },
                        "spec": {"nodeName": "node-a"},
                        "status": {
                            "phase": "Running",
                            "startTime": "2026-08-28T14:00:04Z",
                            "conditions": [
                                {
                                    "type": "PodScheduled",
                                    "status": "True",
                                    "lastTransitionTime": "2026-08-28T14:00:02Z",
                                }
                            ],
                            "containerStatuses": [
                                {
                                    "image": "runner@sha256:abc",
                                    "imageID": "runner@sha256:abc",
                                }
                            ],
                        },
                    }
                ]
            },
            "events": {"items": []},
            "runner_sets": {
                "items": [
                    {
                        "metadata": {
                            "name": "xcsh-compute",
                            "namespace": "arc-runners-xcsh-compute",
                        },
                        "status": {"desiredRunners": 1, "currentRunners": 1},
                    }
                ]
            },
            "azure_quotas": [
                {
                    "name": {"value": "standardDADSv5Family"},
                    "currentValue": 100,
                    "limit": 600,
                }
            ],
        }
        summary = MODULE.summarize_kubernetes(resources)
        jobs = [
            {
                "job_id": 1,
                "runner_name": "runner-a",
                "labels": ["xcsh-compute"],
                "queued_at": "2026-08-28T14:00:00Z",
            }
        ]
        sample = MODULE.correlate_jobs(jobs, summary)[0]
        self.assertTrue(sample["warm"])
        self.assertEqual(2, sample["pod_schedule_seconds"])
        self.assertEqual({"cpu": "250m", "memory": "4Gi"}, summary["nodes"][0]["usage"])
        self.assertEqual({"cpu": "500m", "memory": "2Gi"}, sample["pod"]["usage"])
        self.assertEqual(1, summary["runner_sets"][0]["desired"])
        self.assertEqual(600, summary["quotas"][0]["limit"])

    def test_kubernetes_summary_accepts_null_pending_status(self) -> None:
        resources = {
            "node_metrics": [],
            "pod_metrics": [],
            "nodes": {
                "items": [
                    {
                        "metadata": {"name": "pending-node"},
                        "spec": {},
                        "status": {"conditions": None},
                    }
                ]
            },
            "pods": {
                "items": [
                    {
                        "metadata": {"name": "pending-runner"},
                        "spec": {},
                        "status": {"conditions": None, "containerStatuses": None},
                    }
                ]
            },
            "events": {
                "items": [],
            },
            "runner_sets": {"items": []},
            "azure_quotas": [],
        }
        summary = MODULE.summarize_kubernetes(resources)
        self.assertFalse(summary["nodes"][0]["schedulable"])
        self.assertEqual([], summary["pods"][0]["images"])

    def test_slo_breaches_must_be_consecutive_and_unknown_warmth_is_ignored(
        self,
    ) -> None:
        policy = {
            "timezone": "America/Toronto",
            "service_window": {"start_hour": 6, "end_hour": 22},
            "sample_minutes": 5,
            "consecutive_slo_breaches": 2,
            "slo_seconds": {"warm_assignment_p95": 20, "cold_assignment_p95": 180},
            "alerts": {
                "pending_at_pool_max_seconds": 120,
                "job_wait_seconds": 600,
                "minimum_quota_headroom_ratio": 0.2,
            },
        }
        samples = [
            {
                "queued_at": "2026-08-28T14:00:00Z",
                "started_at": "2026-08-28T14:00:30Z",
                "warm": True,
            },
            {
                "queued_at": "2026-08-28T14:10:00Z",
                "started_at": "2026-08-28T14:10:30Z",
                "warm": True,
            },
            {
                "queued_at": "2026-08-28T14:15:00Z",
                "started_at": "2026-08-28T14:20:00Z",
                "warm": None,
                "queue_seconds": 700,
            },
        ]
        result = MODULE.evaluate(
            samples,
            policy,
            datetime(2026, 8, 28, 14, 20, tzinfo=UTC),
        )
        self.assertNotIn(
            "assignment_slo", {alert["kind"] for alert in result["alerts"]}
        )


if __name__ == "__main__":
    unittest.main()
