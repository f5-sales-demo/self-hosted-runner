#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "arc_capacity", ROOT / "scripts/arc-capacity.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ArcCapacityTests(unittest.TestCase):
    def test_github_jobs_can_select_exact_runs_without_listing_repository_runs(
        self,
    ) -> None:
        run = {
            "id": 34440550597,
            "run_attempt": 1,
            "created_at": "2026-09-10T05:18:37Z",
        }
        job = {
            "id": 456,
            "name": "benchmark",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-10T05:29:57Z",
            "started_at": "2026-09-10T05:30:02Z",
            "completed_at": "2026-09-10T05:46:28Z",
            "runner_name": "xcsh-compute-runner",
            "runner_group_name": "Default",
            "labels": ["xcsh-compute"],
            "steps": [],
        }
        with mock.patch.object(
            MODULE, "command_json", side_effect=[run, [{"jobs": [job]}]]
        ) as command_json:
            result = MODULE.github_jobs(
                "f5-sales-demo/xcsh",
                datetime(2026, 9, 10, tzinfo=UTC),
                10,
                [34440550597],
            )

        self.assertEqual(34440550597, result[0]["run_id"])
        self.assertEqual(
            [
                "gh",
                "api",
                "repos/f5-sales-demo/xcsh/actions/runs/34440550597",
            ],
            command_json.call_args_list[0].args[0],
        )
        self.assertNotIn("actions/runs?", str(command_json.call_args_list))

    def test_queued_github_job_has_no_assignment_until_a_runner_exists(self) -> None:
        run = {
            "id": 123,
            "run_attempt": 1,
            "created_at": "2026-09-10T05:00:00Z",
        }
        queued_job = {
            "id": 456,
            "name": "queued benchmark",
            "status": "queued",
            "conclusion": None,
            "created_at": "2026-09-10T05:01:00Z",
            "started_at": "2026-09-10T05:01:00Z",
            "completed_at": None,
            "runner_name": None,
            "runner_group_name": None,
            "labels": ["xcsh-compute"],
            "steps": [],
        }
        responses = [
            [{"workflow_runs": [run]}],
            [{"jobs": [queued_job]}],
        ]
        with mock.patch.object(MODULE, "command_json", side_effect=responses):
            result = MODULE.github_jobs(
                "f5-sales-demo/xcsh", datetime(2026, 9, 10, tzinfo=UTC), 10
            )

        self.assertEqual("queued", result[0]["status"])
        self.assertIsNone(result[0]["started_at"])
        self.assertIsNone(result[0]["assignment_seconds"])

    def test_workload_artifact_ignores_non_profile_json_and_aggregate_copy(
        self,
    ) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("profiles/install.json", json.dumps({"profile": 1}))
            archive.writestr(
                "node-filesystem.json",
                json.dumps(
                    {
                        "filesystem_bytes": 100,
                        "used_bytes": 50,
                        "available_bytes": 45,
                        "used_ratio": 0.5,
                    }
                ),
            )
            archive.writestr("workload-profiles.json", json.dumps([{"profile": 1}]))
        artifact_page = {
            "artifacts": [
                {
                    "id": 789,
                    "name": "workload-profile-software-baseline-cold-1",
                    "created_at": "2026-09-10T05:00:00Z",
                    "expired": False,
                }
            ]
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=archive_bytes.getvalue(), stderr=b""
        )

        def validate(value: object) -> dict:
            if value != {"profile": 1}:
                raise ValueError("not a workload profile")
            return value

        with (
            mock.patch.object(MODULE, "command_json", return_value=[artifact_page]),
            mock.patch.object(MODULE.subprocess, "run", return_value=completed),
            mock.patch.object(
                MODULE, "validate_workload_profile", side_effect=validate
            ),
        ):
            profiles, filesystems, rejected = MODULE.github_workload_profiles(
                "f5-sales-demo/xcsh", datetime(2026, 9, 10, tzinfo=UTC)
            )

        self.assertEqual([{"profile": 1}], profiles)
        self.assertEqual(0.5, filesystems[0]["used_ratio"])
        self.assertTrue(filesystems[0]["disk_below_70_percent"])
        self.assertEqual([], rejected)

    def test_workload_artifacts_are_queried_and_filtered_by_exact_run(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr(
                "profiles/selected.json",
                json.dumps({"run_id": "34440550597", "profile": 1}),
            )
            archive.writestr(
                "profiles/other.json",
                json.dumps({"run_id": "34440000000", "profile": 2}),
            )
        artifact_page = {
            "artifacts": [
                {
                    "id": 789,
                    "name": "workload-profile-software-baseline-cold-1",
                    "created_at": "2026-09-10T05:00:00Z",
                    "expired": False,
                }
            ]
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=archive_bytes.getvalue(), stderr=b""
        )

        with (
            mock.patch.object(
                MODULE, "command_json", return_value=[artifact_page]
            ) as command_json,
            mock.patch.object(MODULE.subprocess, "run", return_value=completed),
            mock.patch.object(
                MODULE, "validate_workload_profile", side_effect=lambda value: value
            ),
        ):
            profiles, filesystems, rejected = MODULE.github_workload_profiles(
                "f5-sales-demo/xcsh",
                datetime(2026, 9, 11, tzinfo=UTC),
                run_ids=[34440550597],
            )

        self.assertEqual([{"run_id": "34440550597", "profile": 1}], profiles)
        self.assertEqual([], filesystems)
        self.assertEqual([], rejected)
        self.assertEqual(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                "repos/f5-sales-demo/xcsh/actions/runs/34440550597/artifacts?per_page=100",
            ],
            command_json.call_args.args[0],
        )

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

    def test_warm_classification_uses_the_runner_pods_selected_node(self) -> None:
        demanded = datetime(2026, 8, 28, 14, tzinfo=UTC)
        nodes = [
            {
                "name": "already-ready-but-full",
                "profile": "compute",
                "schedulable": True,
                "ready_at": "2026-08-28T13:00:00Z",
                "removed_at": None,
            },
            {
                "name": "newly-scaled-node",
                "profile": "compute",
                "schedulable": True,
                "ready_at": "2026-08-28T14:02:00Z",
                "removed_at": None,
            },
        ]

        self.assertTrue(MODULE.classify_warm(demanded, nodes, "compute"))
        self.assertFalse(
            MODULE.classify_warm(demanded, nodes, "compute", "newly-scaled-node")
        )

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
                "assignment_seconds": 30,
                "warm": True,
            },
            {
                "job_id": 2,
                "queued_at": "2026-08-28T14:05:00Z",
                "started_at": "2026-08-28T14:05:25Z",
                "assignment_seconds": 25,
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
                                    "lastTransitionTime": "2026-08-28T14:00:00.500000Z",
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
                "started_at": "2026-08-28T14:00:05Z",
                "assignment_seconds": 5,
            }
        ]
        sample = MODULE.correlate_jobs(jobs, summary)[0]
        self.assertFalse(sample["warm"])
        self.assertEqual(4, sample["assignment_seconds"])
        self.assertEqual(5, sample["github_queue_seconds"])
        self.assertEqual(1, sample["pod_schedule_seconds"])
        self.assertTrue(sample["assignment_slo_eligible"])
        self.assertEqual({"cpu": "250m", "memory": "4Gi"}, summary["nodes"][0]["usage"])
        self.assertEqual({"cpu": "500m", "memory": "2Gi"}, sample["pod"]["usage"])
        self.assertEqual(1, summary["runner_sets"][0]["desired"])
        self.assertEqual(600, summary["quotas"][0]["limit"])

    def test_assignment_summary_enforces_warm_and_cold_p95(self) -> None:
        queued = datetime(2026, 9, 10, 14, tzinfo=UTC)
        samples = []
        for warm, delays in (
            (True, (10, 12, 14, 16, 18)),
            (False, (100, 120, 140, 160, 170)),
        ):
            for index, delay in enumerate(delays):
                started = queued + timedelta(seconds=delay)
                samples.append(
                    {
                        "job_id": f"{warm}-{index}",
                        "queued_at": queued.isoformat(),
                        "started_at": started.isoformat(),
                        "assignment_seconds": delay,
                        "assignment_slo_eligible": True,
                        "warm": warm,
                    }
                )
        policy = {
            "slo_seconds": {
                "warm_assignment_p95": 20,
                "cold_assignment_p95": 180,
            }
        }

        reports = {
            item["class"]: item
            for item in MODULE.assignment_slo_summary(samples, policy)
        }
        self.assertTrue(reports["warm"]["qualifies"])
        self.assertTrue(reports["cold"]["qualifies"])

        samples[-1]["assignment_seconds"] = 300
        reports = {
            item["class"]: item
            for item in MODULE.assignment_slo_summary(samples, policy)
        }
        self.assertFalse(reports["cold"]["qualifies"])

    def test_candidate_runner_profiles_map_to_underlying_node_profiles(self) -> None:
        self.assertEqual(
            "compute",
            MODULE.node_profile_for_runner("compute-bun-candidate"),
        )
        self.assertEqual(
            "compute-f32",
            MODULE.node_profile_for_runner("compute-f32-candidate"),
        )
        self.assertEqual(
            "socketless",
            MODULE.node_profile_for_runner("socketless"),
        )

    def test_candidate_labels_and_fsv2_quota_are_retained(self) -> None:
        self.assertEqual(
            "compute-bun-candidate",
            MODULE.managed_profile(["xcsh-compute-bun-candidate"]),
        )
        self.assertEqual(
            "compute-f32-candidate",
            MODULE.managed_profile(["xcsh-compute-f32-candidate"]),
        )
        resources = {
            "node_metrics": [],
            "pod_metrics": [],
            "nodes": {"items": []},
            "pods": {"items": []},
            "events": {"items": []},
            "runner_sets": {"items": []},
            "azure_quotas": [
                {
                    "name": {"value": "standardFSv2Family"},
                    "currentValue": 0,
                    "limit": 350,
                }
            ],
        }
        self.assertEqual(
            "standardFSv2Family",
            MODULE.summarize_kubernetes(resources)["quotas"][0]["name"],
        )

    def test_live_candidate_pod_keeps_runner_profile_not_node_profile(self) -> None:
        resources = {
            "node_metrics": [],
            "pod_metrics": [],
            "nodes": {
                "items": [
                    {
                        "metadata": {
                            "name": "d16-node",
                            "labels": {"runner-profile": "compute"},
                        },
                        "spec": {},
                        "status": {"conditions": []},
                    }
                ]
            },
            "pods": {
                "items": [
                    {
                        "metadata": {
                            "name": "candidate-runner",
                            "namespace": "arc-runners-xcsh-compute-bun-candidate",
                            "labels": {
                                "actions.github.com/scale-set-name": "xcsh-compute-bun-candidate"
                            },
                        },
                        "spec": {"nodeName": "d16-node"},
                        "status": {"conditions": [], "containerStatuses": []},
                    }
                ]
            },
            "events": {"items": []},
            "runner_sets": {"items": []},
            "azure_quotas": [],
        }

        summary = MODULE.summarize_kubernetes(resources)
        self.assertEqual("compute-bun-candidate", summary["pods"][0]["profile"])

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

    def test_deleted_runner_pod_watch_retains_assignment_evidence(self) -> None:
        pod = {
            "namespace": "arc-runners-xcsh-compute-bun-candidate",
            "name": "xcsh-compute-bun-candidate-runner-a",
            "created_at": "2026-09-10T09:00:00Z",
            "deleted_at": "2026-09-10T09:20:00Z",
            "scale_set": "xcsh-compute-bun-candidate",
            "node": "compute-node-a",
            "phase": "Succeeded",
            "started_at": "2026-09-10T09:00:04Z",
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": "True",
                    "lastTransitionTime": "2026-09-10T09:00:01Z",
                }
            ],
            "container_statuses": [
                {
                    "image": "runner@sha256:abc",
                    "imageID": "runner@sha256:abc",
                }
            ],
        }
        events = [
            {
                "observed_at": "2026-09-10T09:00:00Z",
                "event_type": "ADDED",
                "pod": {**pod, "phase": "Pending", "deleted_at": None},
            },
            {
                "observed_at": "2026-09-10T09:20:00Z",
                "event_type": "DELETED",
                "pod": pod,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            watch = Path(directory) / "pod-watch.jsonl"
            watch.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            observed = MODULE.load_pod_watch(watch)
            node_watch = Path(directory) / "node-watch.jsonl"
            node_watch.write_text(
                json.dumps(
                    {
                        "observed_at": "2026-09-10T09:30:00Z",
                        "event_type": "DELETED",
                        "node": {
                            "name": "compute-node-a",
                            "created_at": "2026-09-10T08:58:00Z",
                            "profile": "compute",
                            "unschedulable": False,
                            "allocatable": {"cpu": "15740m", "memory": "62Gi"},
                            "conditions": [
                                {
                                    "type": "Ready",
                                    "status": "True",
                                    "lastTransitionTime": "2026-09-10T08:59:00Z",
                                }
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            observed_nodes = MODULE.load_node_watch(node_watch)

        summary = {
            "nodes": [],
            "pods": [],
            "runner_sets": [],
            "quotas": [],
        }
        MODULE.merge_observed_nodes(summary, observed_nodes["nodes"])
        MODULE.merge_observed_pods(summary, observed["pods"])
        sample = MODULE.correlate_jobs(
            [
                {
                    "job_id": 1,
                    "runner_name": "xcsh-compute-bun-candidate-runner-a",
                    "labels": ["xcsh-compute-bun-candidate"],
                    "queued_at": "2026-09-10T08:59:58Z",
                    "started_at": "2026-09-10T09:00:05Z",
                    "assignment_seconds": 7,
                }
            ],
            summary,
        )[0]

        self.assertEqual(2, observed["event_count"])
        self.assertEqual(1, observed["pod_count"])
        self.assertRegex(observed["sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(1, observed_nodes["event_count"])
        self.assertEqual(1, observed_nodes["node_count"])
        self.assertEqual("2026-09-10T09:30:00Z", summary["nodes"][0]["removed_at"])
        self.assertTrue(sample["warm"])
        self.assertEqual(5, sample["assignment_seconds"])
        self.assertEqual(7, sample["github_queue_seconds"])
        self.assertEqual(1, sample["pod_schedule_seconds"])
        self.assertEqual("Succeeded", sample["pod"]["phase"])
        self.assertEqual("2026-09-10T09:20:00Z", sample["pod"]["observed_deleted_at"])

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
                "assignment_seconds": 700,
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
