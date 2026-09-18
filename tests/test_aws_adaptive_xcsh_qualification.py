from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "qualification", ROOT / "scripts/aws-adaptive-xcsh-qualification.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def evidence(role: str, workers: int, *, typescript: float, critical: float) -> dict:
    return {
        "run_id": 7 if role.endswith("serial") else 8,
        "role": role,
        "workers": workers,
        "source_sha": "a" * 40,
        "image_digest": "image@sha256:" + "b" * 64,
        "output_equivalent": True,
        "manifest_equivalent": True,
        "inventory_complete": True,
        "failures": 0,
        "ooms": 0,
        "evictions": 0,
        "restarts": 0,
        "memory_ratio": 0.7,
        "node_pressure": False,
        "typescript_seconds": typescript,
        "critical_path_seconds": critical,
        "pod_cpu": "30" if workers else "14",
        "pod_memory": "56Gi" if workers else "48Gi",
        "aws_quota": 660,
    }


class QualificationTests(unittest.TestCase):
    def test_dispatches_exactly_one_serial_then_parallel_20_pair(self) -> None:
        state = {"evidence": []}
        self.assertEqual(
            ("d16-serial", 0),
            tuple(
                MODULE.next_dispatch(state)[key] for key in ("experiment", "workers")
            ),
        )
        state["evidence"].append({"role": "qualification-serial"})
        self.assertEqual(
            ("f32-parallel", 20),
            tuple(
                MODULE.next_dispatch(state)[key] for key in ("experiment", "workers")
            ),
        )

    def test_identity_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.json"
            state.write_text(
                json.dumps(
                    {"source_sha": "c" * 40, "image_digest": "image@sha256:" + "d" * 64}
                )
            )
            with self.assertRaises(ValueError):
                MODULE.load_state(state, "a" * 40, "image@sha256:" + "b" * 64)

    def test_candidate_requires_guaranteed_resources_and_approved_quota(self) -> None:
        state = {
            "source_sha": "a" * 40,
            "image_digest": "image@sha256:" + "b" * 64,
            "evidence": [],
        }
        invalid = evidence("qualification-candidate", 20, typescript=70, critical=140)
        invalid["pod_cpu"] = "15"
        with self.assertRaisesRegex(ValueError, "30 CPU"):
            MODULE.record_evidence(state, invalid)
        invalid = evidence("qualification-candidate", 20, typescript=70, critical=140)
        invalid["aws_quota"] = 655
        with self.assertRaisesRegex(ValueError, "660-vCPU"):
            MODULE.record_evidence(state, invalid)

    def test_pair_requires_safety_and_both_durations_to_decrease(self) -> None:
        state = {
            "evidence": [
                {
                    **evidence("qualification-serial", 0, typescript=100, critical=200),
                    "safe": True,
                },
                {
                    **evidence(
                        "qualification-candidate", 20, typescript=80, critical=150
                    ),
                    "safe": True,
                },
            ]
        }
        self.assertTrue(MODULE.evaluate_qualification(state)["promotable"])
        state["evidence"][1]["critical_path_seconds"] = 200
        self.assertFalse(MODULE.evaluate_qualification(state)["promotable"])

    def test_dispatch_uses_main_and_actual_f32_label_experiment(self) -> None:
        source_sha = "a" * 40
        action = {
            "experiment": "f32-parallel",
            "workers": 20,
            "cache_state": "cold",
            "pair_id": 1,
        }
        command = MODULE.dispatch_command(source_sha, action)
        self.assertEqual("main", command[command.index("--ref") + 1])
        self.assertIn(f"source_sha={source_sha}", command)
        self.assertIn("experiment=f32-parallel", command)
        self.assertIn("file_workers=20", command)


if __name__ == "__main__":
    unittest.main()
