from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "arc_capacity", ROOT / "scripts/arc-capacity.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def profile(
    variant: str, pair: str, duration: float, digest: str = "same", ratio: float = 0.5
) -> dict:
    return {
        "repository": "f5-sales-demo/example",
        "commit": "a" * 40,
        "phase": "test",
        "variant": variant,
        "pair_id": pair,
        "cache_state": "warm",
        "runner_profile": "compute",
        "image_digest": "sha256:" + "b" * 64,
        "duration_seconds": duration,
        "output_digest": digest,
        "exit": {"code": 0},
        "cpu": {
            "utilization_ratio": 0.5,
            "nr_periods": 100,
            "nr_throttled": 5,
        },
        "io": {"rbytes": 1024, "wbytes": 2048},
        "memory": {"peak_limit_ratio": ratio, "events": {"oom_kill": 0}},
    }


class WorkloadReportTests(unittest.TestCase):
    def test_five_paired_runs_must_meet_every_gate(self) -> None:
        profiles = []
        for index in range(5):
            profiles.extend(
                (
                    profile("baseline", str(index), 100 + index),
                    profile("candidate", str(index), 70 + index),
                )
            )
        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertTrue(comparison["qualifies"])
        self.assertGreaterEqual(comparison["median_improvement_ratio"], 0.2)
        report = next(
            item
            for item in MODULE.aggregate_workload_profiles(profiles)
            if item["variant"] == "baseline"
        )
        self.assertEqual(0.5, report["median_cpu_utilization_ratio"])
        self.assertEqual(1024, report["median_io_read_bytes"])
        self.assertEqual(2048, report["median_io_write_bytes"])
        self.assertEqual(["sha256:" + "b" * 64], report["image_digests"])
        self.assertEqual(["same"], report["output_digests"])
        profiles[-1]["output_digest"] = "different"
        self.assertFalse(MODULE.performance_comparisons(profiles)[0]["qualifies"])

    def test_pairwise_output_matches_must_also_be_repeatable(self) -> None:
        profiles = []
        for index in range(5):
            digest = f"pair-{index}"
            profiles.extend(
                (
                    profile("baseline", str(index), 100, digest=digest),
                    profile("f32", str(index), 70, digest=digest),
                )
            )

        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertTrue(comparison["output_equivalent"])
        self.assertFalse(comparison["outputs_repeatable"])
        self.assertFalse(comparison["qualifies"])

    def test_fewer_than_five_or_memory_at_eighty_percent_fails(self) -> None:
        profiles = []
        for index in range(4):
            profiles.extend(
                (
                    profile("baseline", str(index), 100),
                    profile("candidate", str(index), 60, ratio=0.8),
                )
            )
        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertFalse(comparison["qualifies"])
        self.assertFalse(comparison["memory_below_80_percent"])
        profiles = []
        for index in range(5):
            profiles.extend(
                (
                    profile("baseline", str(index), 100),
                    profile("candidate", str(index), 60, ratio=None),
                )
            )
        self.assertFalse(MODULE.performance_comparisons(profiles)[0]["qualifies"])

    def test_four_paired_burst_slots_meet_the_burst_sample_gate(self) -> None:
        profiles = []
        for index in range(4):
            baseline = profile("baseline", str(index), 100 + index)
            baseline["phase"] = "test-burst"
            candidate = profile("f32", str(index), 70 + index)
            candidate["phase"] = "test-burst"
            profiles.extend((baseline, candidate))

        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertEqual(4, comparison["paired_runs"])
        self.assertEqual(4, comparison["required_pairs"])
        self.assertTrue(comparison["qualifies"])

    def test_bun_candidate_requires_no_regression_not_twenty_percent(self) -> None:
        profiles = []
        for index in range(5):
            baseline = profile("baseline", str(index), 100 + index)
            candidate = profile("bun-1.4.2", str(index), 99 + index)
            candidate["image_digest"] = "sha256:" + "c" * 64
            profiles.extend((baseline, candidate))

        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertEqual(0.0, comparison["minimum_median_improvement_ratio"])
        self.assertTrue(comparison["immutable_image_evidence"])
        self.assertTrue(comparison["hardware_image_equivalent"])
        self.assertTrue(comparison["qualifies"])

        profiles[-1]["duration_seconds"] = 200
        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertFalse(comparison["qualifies"])

    def test_hardware_comparison_rejects_image_or_commit_drift(self) -> None:
        profiles = []
        for index in range(5):
            profiles.extend(
                (
                    profile("baseline", str(index), 100),
                    profile("f32", str(index), 70),
                )
            )

        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertTrue(comparison["frozen_commit"])
        self.assertTrue(comparison["hardware_image_equivalent"])
        self.assertTrue(comparison["qualifies"])

        profiles[-1]["image_digest"] = "sha256:" + "c" * 64
        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertFalse(comparison["hardware_image_equivalent"])
        self.assertFalse(comparison["qualifies"])

        profiles[-1]["image_digest"] = "sha256:" + "b" * 64
        profiles[-1]["commit"] = "d" * 40
        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertFalse(comparison["frozen_commit"])
        self.assertFalse(comparison["qualifies"])

    def test_sustained_cpu_throttling_regression_fails_candidate(self) -> None:
        profiles = []
        for index in range(5):
            baseline = profile("baseline", str(index), 100)
            candidate = profile("f32", str(index), 70)
            candidate["cpu"] = {"nr_periods": 100, "nr_throttled": 20}
            profiles.extend((baseline, candidate))

        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertEqual(0.05, comparison["baseline_median_cpu_throttle_ratio"])
        self.assertEqual(0.2, comparison["candidate_median_cpu_throttle_ratio"])
        self.assertFalse(comparison["no_sustained_cpu_throttling_regression"])
        self.assertFalse(comparison["qualifies"])

        for candidate in profiles[1::2]:
            candidate["cpu"] = {"nr_periods": 1000, "nr_throttled": 59}
        comparison = MODULE.performance_comparisons(profiles)[0]
        self.assertTrue(comparison["no_sustained_cpu_throttling_regression"])
        self.assertTrue(comparison["qualifies"])

    def test_pod_stability_reports_failures_evictions_restarts_and_ooms(self) -> None:
        pods = [
            {
                "namespace": "arc-runners-xcsh-compute-f32-candidate",
                "profile": "compute-f32-candidate",
                "phase": "Succeeded",
                "reason": None,
                "restart_count": 0,
                "termination_reasons": ["Completed"],
            },
            {
                "namespace": "arc-runners-xcsh-compute-f32-candidate",
                "profile": "compute-f32-candidate",
                "phase": "Failed",
                "reason": "Evicted",
                "restart_count": 1,
                "termination_reasons": ["OOMKilled"],
            },
            {
                "namespace": "arc-systems",
                "profile": "compute-f32-candidate",
                "phase": "Running",
                "reason": None,
                "restart_count": 0,
                "termination_reasons": [],
            },
        ]

        summary = MODULE.summarize_pod_stability(pods)[0]
        self.assertEqual(2, summary["pods"])
        self.assertEqual(1, summary["failed_pods"])
        self.assertEqual(1, summary["evictions"])
        self.assertEqual(1, summary["container_restarts"])
        self.assertEqual(1, summary["oom_kills"])
        self.assertFalse(summary["stable"])

    def test_f32_cotenancy_requires_four_successes_and_observed_overlap(self) -> None:
        samples = []
        for slot in range(1, 5):
            node = "f32-node-a" if slot <= 2 else "f32-node-b"
            samples.append(
                {
                    "name": f"F32 four-job burst / slot-{slot}",
                    "conclusion": "success",
                    "runner_name": f"runner-{slot}",
                    "started_at": "2026-09-10T12:00:00Z",
                    "completed_at": "2026-09-10T12:10:00Z",
                    "pod": {"node": node},
                }
            )

        summary = MODULE.f32_cotenancy_summary(samples)
        self.assertEqual(4, summary["jobs"])
        self.assertEqual(4, summary["successful_jobs"])
        self.assertEqual(4, summary["correlated_jobs"])
        self.assertEqual(4, summary["unique_runners"])
        self.assertEqual(2, summary["nodes"])
        self.assertEqual(2, summary["maximum_concurrent_runners_per_node"])
        self.assertEqual(
            ["f32-node-a", "f32-node-b"], summary["nodes_with_two_runner_overlap"]
        )
        self.assertTrue(summary["observed"])
        self.assertTrue(summary["qualifies"])

        for slot, sample in enumerate(samples, 1):
            sample["pod"] = {"node": f"f32-node-{slot}"}
        summary = MODULE.f32_cotenancy_summary(samples)
        self.assertEqual(1, summary["maximum_concurrent_runners_per_node"])
        self.assertFalse(summary["observed"])
        self.assertFalse(summary["qualifies"])

        samples[-1]["conclusion"] = "failure"
        samples[-1]["pod"] = {"node": "f32-node-1"}
        summary = MODULE.f32_cotenancy_summary(samples)
        self.assertTrue(summary["observed"])
        self.assertFalse(summary["qualifies"])

    def test_profile_schema_validation_rejects_missing_or_invalid_fields(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.validate_workload_profile({"schema_version": 1})
        valid = {
            "schema_version": 1,
            "repository": "example/repo",
            "commit": None,
            "run_id": "1",
            "run_attempt": "1",
            "job_id": "test",
            "runner_name": None,
            "runner_profile": "socketless",
            "image_digest": None,
            "phase": "test",
            "variant": "baseline",
            "pair_id": "1",
            "cache_state": "warm",
            "started_at": "2026-08-28T00:00:00Z",
            "completed_at": "2026-08-28T00:00:01Z",
            "duration_seconds": 1,
            "sample_count": 1,
            "phase_timings": [{"name": "test", "duration_seconds": 1}],
            "cpu": {
                "usage_usec": 1,
                "user_usec": 1,
                "system_usec": 0,
                "utilization_ratio": 0.1,
                "nr_periods": 1,
                "nr_throttled": 0,
                "throttled_usec": 0,
            },
            "memory": {
                "current_bytes": 1,
                "peak_bytes": 2,
                "limit_bytes": 4,
                "peak_limit_ratio": 0.5,
                "events": {"oom_kill": 0},
            },
            "io": {"rbytes": 0},
            "output_digest": None,
            "exit": {"code": 0, "signal": None},
        }
        self.assertIs(valid, MODULE.validate_workload_profile(valid))
        for mutation in (None, {"oom_kill": None}, {"oom_kill": "1"}):
            invalid = {**valid, "memory": {**valid["memory"], "events": mutation}}
            with self.subTest(mutation=mutation), self.assertRaises(TypeError):
                MODULE.validate_workload_profile(invalid)

    def test_docker_action_schema_and_aggregation(self) -> None:
        digest = "sha256:" + "a" * 64
        image_id = "sha256:" + "b" * 64
        valid = {
            "schema_version": 1,
            "profile_kind": "docker_action",
            "repository": "example/repo",
            "commit": "c" * 40,
            "run_id": "7",
            "run_attempt": "1",
            "job_id": "lint",
            "runner_name": "runner",
            "runner_profile": "container-build",
            "runner_image_digest": digest,
            "phase": "super-linter",
            "variant": "baseline",
            "pair_id": "1",
            "cache_state": "cold",
            "started_at": "2026-08-28T00:00:00Z",
            "completed_at": "2026-08-28T00:00:10Z",
            "duration_seconds": 10,
            "sample_count": 2,
            "image": {"id": image_id, "digest": digest, "size_bytes": 1000},
            "cpu": {
                "usage_seconds": 5.0,
                "mean_utilization_ratio": 0.5,
                "peak_utilization_ratio": 0.7,
            },
            "memory": {
                "peak_bytes": 500,
                "limit_bytes": 1000,
                "peak_limit_ratio": 0.5,
                "oom": False,
            },
            "block_io": {"read_bytes": 10, "write_bytes": 20},
            "network_io": {"receive_bytes": 30, "transmit_bytes": 40},
            "pids": {"peak": 8},
            "exit": {"code": 0, "signal": None},
            "observer": {
                "result": "completed",
                "detail": "container_exit_observed",
            },
        }
        self.assertIs(valid, MODULE.validate_workload_profile(valid))
        report = MODULE.aggregate_workload_profiles([valid])[0]
        self.assertEqual(1000, report["median_image_bytes"])
        self.assertEqual(5.0, report["median_cpu_seconds"])
        self.assertEqual(8, report["max_pids"])
        self.assertEqual(30, report["network_receive_bytes"])
        self.assertEqual([], MODULE.performance_comparisons([valid]))
        failed = {**valid, "exit": {"code": 23, "signal": None}}
        self.assertEqual(1, MODULE.aggregate_workload_profiles([failed])[0]["failures"])
        invalid = {**valid, "image": {**valid["image"], "digest": "latest"}}
        with self.assertRaises(TypeError):
            MODULE.validate_workload_profile(invalid)
        invalid = {**valid, "block_io": {"read_bytes": 10}}
        with self.assertRaises(TypeError):
            MODULE.validate_workload_profile(invalid)

    def test_job_timing_reports_classify_lint_phases(self) -> None:
        jobs = [
            {
                "repository": "example/repo",
                "name": "lint / Lint Code Base",
                "dependency_wait_seconds": 3,
                "assignment_seconds": 7,
                "duration_seconds": 40,
                "steps": [
                    {"name": "Set up job", "duration_seconds": 5},
                    {"name": "Checkout", "duration_seconds": 2},
                    {"name": "Check repository hygiene", "duration_seconds": 4},
                    {"name": "Super-Linter", "duration_seconds": 20},
                    {"name": "Spectral OpenAPI lint", "duration_seconds": 6},
                    {"name": "Upload artifact", "duration_seconds": 1},
                ],
            }
        ]
        reports = MODULE.aggregate_job_timings(jobs)
        phases = {report["phase"]: report["median_seconds"] for report in reports}
        self.assertEqual(7, phases["runner_assignment"])
        self.assertEqual(5, phases["action_preparation"])
        self.assertEqual(2, phases["checkout"])
        self.assertEqual(4, phases["native_prechecks"])
        self.assertEqual(20, phases["super_linter"])
        self.assertEqual(6, phases["spectral"])
        self.assertEqual(1, phases["post_processing"])

    def test_burst_clearance_requires_improvement_and_no_runtime_regression(
        self,
    ) -> None:
        jobs = []
        for slot in range(1, 5):
            baseline_completed = 60 if slot <= 2 else 120
            jobs.append(
                {
                    "repository": "f5-sales-demo/xcsh",
                    "run_id": 123,
                    "name": f"Current D16 two-slot burst / slot-{slot}",
                    "queued_at": "2026-09-10T09:00:00Z",
                    "completed_at": f"2026-09-10T09:{baseline_completed // 60:02d}:{baseline_completed % 60:02d}Z",
                    "duration_seconds": 60,
                    "conclusion": "success",
                }
            )
            jobs.append(
                {
                    "repository": "f5-sales-demo/xcsh",
                    "run_id": 123,
                    "name": f"Candidate D16 four-slot burst / slot-{slot}",
                    "queued_at": "2026-09-10T09:00:00Z",
                    "completed_at": "2026-09-10T09:01:20Z",
                    "duration_seconds": 50,
                    "conclusion": "success",
                }
            )
            jobs.append(
                {
                    "repository": "f5-sales-demo/xcsh",
                    "run_id": 123,
                    "name": f"F32 four-job burst / slot-{slot}",
                    "queued_at": "2026-09-10T09:00:00Z",
                    "completed_at": "2026-09-10T09:01:10Z",
                    "duration_seconds": 45,
                    "conclusion": "success",
                }
            )

        comparisons = {
            item["variant"]: item for item in MODULE.burst_clearance_comparisons(jobs)
        }
        self.assertEqual({"d16-four", "f32"}, set(comparisons))
        self.assertAlmostEqual(
            1 / 3, comparisons["d16-four"]["clearance_improvement_ratio"]
        )
        self.assertTrue(comparisons["d16-four"]["no_p95_runtime_regression"])
        self.assertTrue(comparisons["d16-four"]["qualifies"])
        self.assertTrue(comparisons["f32"]["qualifies"])

    def test_price_evidence_enforces_peak_and_per_workflow_cost_gates(self) -> None:
        payload = {
            "collected_at": "2026-09-10T09:55:33Z",
            "source": "https://prices.azure.com/api/retail/prices",
            "region": "canadacentral",
            "currency": "USD",
            "d16": {
                "armSkuName": "Standard_D16ads_v5",
                "currencyCode": "USD",
                "unitOfMeasure": "1 Hour",
                "unitPrice": 0.92,
            },
            "f32": {
                "armSkuName": "Standard_F32s_v2",
                "currencyCode": "USD",
                "unitOfMeasure": "1 Hour",
                "unitPrice": 1.482,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "retail-prices.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            pricing = MODULE.load_price_evidence(path)

        self.assertEqual(0.741, pricing["runner_slot_hourly_rates"]["f32"])
        self.assertAlmostEqual(8.28, pricing["candidate_peak_hourly_costs"]["d16-four"])
        self.assertAlmostEqual(7.41, pricing["candidate_peak_hourly_costs"]["f32"])
        self.assertAlmostEqual(9.2, pricing["maximum_peak_hourly_cost"])
        self.assertTrue(all(pricing["candidate_peak_below_limit"].values()))
        self.assertRegex(pricing["sha256"], r"^sha256:[0-9a-f]{64}$")

        jobs = []
        variants = {
            "Current D16 two-slot burst": 60,
            "Candidate D16 four-slot burst": 50,
            "F32 four-job burst": 70,
        }
        for name, duration in variants.items():
            for slot in range(1, 5):
                jobs.append(
                    {
                        "repository": "f5-sales-demo/xcsh",
                        "run_id": 123,
                        "name": f"{name} / slot-{slot}",
                        "duration_seconds": duration,
                        "conclusion": "success",
                    }
                )

        comparisons = {
            item["variant"]: item
            for item in MODULE.burst_cost_comparisons(jobs, pricing)
        }
        self.assertTrue(comparisons["d16-four"]["qualifies"])
        self.assertTrue(comparisons["f32"]["qualifies"])
        self.assertLess(
            comparisons["f32"]["candidate"]["cost_per_successful_workflow"],
            comparisons["f32"]["baseline"]["cost_per_successful_workflow"],
        )

        for job in jobs:
            if job["name"].startswith("F32 four-job burst"):
                job["duration_seconds"] = 100
        comparison = {
            item["variant"]: item
            for item in MODULE.burst_cost_comparisons(jobs, pricing)
        }["f32"]
        self.assertFalse(comparison["cost_not_increased"])
        self.assertFalse(comparison["qualifies"])

    def test_price_evidence_requires_exact_region_skus_and_units(self) -> None:
        payload = {
            "region": "canadacentral",
            "currency": "USD",
            "d16": {
                "armSkuName": "Standard_D16ads_v5",
                "currencyCode": "USD",
                "unitOfMeasure": "1 Hour",
                "unitPrice": 0.92,
            },
            "f32": {
                "armSkuName": "Standard_F32s_v2",
                "currencyCode": "USD",
                "unitOfMeasure": "1 Hour",
                "unitPrice": 1.482,
            },
        }
        for field, value in (
            ("region", "eastus"),
            ("currency", "CAD"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                candidate = {**payload, field: value}
                path = Path(directory) / "prices.json"
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(ValueError):
                    MODULE.load_price_evidence(path)

    def test_invalid_artifact_is_rejected_without_partial_profiles(self) -> None:
        valid = {
            "schema_version": 1,
            "repository": "example/repo",
            "commit": None,
            "run_id": "1",
            "run_attempt": "1",
            "job_id": "test",
            "runner_name": None,
            "runner_profile": "socketless",
            "image_digest": None,
            "phase": "test",
            "variant": "baseline",
            "pair_id": "1",
            "cache_state": "warm",
            "started_at": "2026-08-28T00:00:00Z",
            "completed_at": "2026-08-28T00:00:01Z",
            "duration_seconds": 1,
            "sample_count": 1,
            "phase_timings": [{"name": "test", "duration_seconds": 1}],
            "cpu": {
                "usage_usec": 1,
                "user_usec": 1,
                "system_usec": 0,
                "utilization_ratio": 0.1,
                "nr_periods": 1,
                "nr_throttled": 0,
                "throttled_usec": 0,
            },
            "memory": {
                "current_bytes": 1,
                "peak_bytes": 2,
                "limit_bytes": 4,
                "peak_limit_ratio": 0.5,
                "events": {"oom_kill": 0},
            },
            "io": {"rbytes": 0},
            "output_digest": None,
            "exit": {"code": 0, "signal": None},
        }
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("first.json", json.dumps(valid))
            archive.writestr("second.json", '{"schema_version":1}')
        pages = [
            {
                "artifacts": [
                    {
                        "id": 7,
                        "name": "workload-profile-test",
                        "created_at": "2026-08-28T00:00:00Z",
                        "expired": False,
                    }
                ]
            }
        ]
        with (
            mock.patch.object(MODULE, "command_json", return_value=pages),
            mock.patch.object(
                MODULE.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=payload.getvalue()),
            ),
        ):
            profiles, filesystems, rejected = MODULE.github_workload_profiles(
                "example/repo", datetime(2026, 8, 27, tzinfo=UTC)
            )
        self.assertEqual([], profiles)
        self.assertEqual([], filesystems)
        self.assertEqual([{"artifact_id": 7, "reason": "invalid_profile"}], rejected)

    def test_node_filesystem_reports_enforce_the_seventy_percent_gate(self) -> None:
        reports = [
            {
                "repository": "f5-sales-demo/xcsh",
                "variant": "f32",
                "cache_state": "cold",
                "runner_profile": "compute-f32-candidate",
                "used_ratio": ratio,
                "disk_below_70_percent": ratio < 0.7,
            }
            for ratio in (0.56, 0.69)
        ]
        summary = MODULE.aggregate_node_filesystems(reports)[0]
        self.assertEqual(2, summary["runs"])
        self.assertEqual(0.69, summary["max_used_ratio"])
        self.assertTrue(summary["disk_below_70_percent"])

        reports[-1]["used_ratio"] = 0.7
        reports[-1]["disk_below_70_percent"] = False
        self.assertFalse(
            MODULE.aggregate_node_filesystems(reports)[0]["disk_below_70_percent"]
        )

    def test_dependency_wait_is_not_assignment_latency(self) -> None:
        labels = ["managed-socketless"]
        self.assertEqual("socketless", MODULE.managed_profile(labels))
        sample = {
            "assignment_slo_eligible": False,
            "queued_at": "2026-08-28T14:00:00Z",
            "started_at": "2026-08-28T14:20:00Z",
            "assignment_seconds": 1200,
            "warm": True,
        }
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
        result = MODULE.evaluate(
            [sample], policy, MODULE.parse_time("2026-08-28T15:00:00Z")
        )
        self.assertEqual([], result["alerts"])


if __name__ == "__main__":
    unittest.main()
