from __future__ import annotations

import importlib.util
import io
import json
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
        "phase": "test",
        "variant": variant,
        "pair_id": pair,
        "cache_state": "warm",
        "runner_profile": "compute",
        "duration_seconds": duration,
        "output_digest": digest,
        "exit": {"code": 0},
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
        profiles[-1]["output_digest"] = "different"
        self.assertFalse(MODULE.performance_comparisons(profiles)[0]["qualifies"])

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
            profiles, rejected = MODULE.github_workload_profiles(
                "example/repo", datetime(2026, 8, 27, tzinfo=UTC)
            )
        self.assertEqual([], profiles)
        self.assertEqual([{"artifact_id": 7, "reason": "invalid_profile"}], rejected)

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
