from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("adaptive", ROOT / "scripts/aws-adaptive-xcsh-qualification.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AdaptiveQualificationTests(unittest.TestCase):
    def test_searches_upward_then_bisects_unsafe_or_unhelpful_points(self) -> None:
        state = {"probes": []}
        self.assertEqual(10, MODULE.next_probe(state))
        state["probes"].append({"workers": 10, "safe": True, "improvement": .05})
        self.assertEqual(20, MODULE.next_probe(state))
        state["probes"].append({"workers": 20, "safe": False, "improvement": 0})
        self.assertEqual(15, MODULE.next_probe(state))
        self.assertEqual([10, 20], state["bracket"])
        state["probes"].append({"workers": 15, "safe": True, "improvement": .05})
        state["bracket"] = [15, 20]
        self.assertEqual(17, MODULE.next_probe(state))

    def test_less_than_three_percent_increment_bisects_without_probing_upward(self) -> None:
        state = {"probes": [
            {"workers": 10, "safe": True, "improvement": .10},
            {"workers": 20, "safe": True, "improvement": .12},
        ]}
        self.assertEqual(15, MODULE.next_probe(state))
        self.assertEqual([10, 20], state["bracket"])

    def test_uses_40_after_a_useful_30_worker_probe(self) -> None:
        state = {"probes": [
            {"workers": 10, "safe": True, "improvement": .10},
            {"workers": 20, "safe": True, "improvement": .15},
            {"workers": 30, "safe": True, "improvement": .19},
        ]}
        self.assertEqual(40, MODULE.next_probe(state))

    def test_schedules_configured_serial_controls_before_screening_and_rotating_qualification_pairs(self) -> None:
        state = {"status": "screening", "controls": [], "probes": [], "qualification": []}
        self.assertEqual(
            {"role": "screening-control", "experiment": "d16-serial", "workers": 0, "cache_state": "warm", "pair_id": 1},
            MODULE.next_dispatch(state),
        )
        state["controls"] = [{}]
        self.assertEqual("screening-candidate", MODULE.next_dispatch(state)["role"])
        state["controls"] = []
        self.assertEqual("screening-control", MODULE.next_dispatch(state, screening_controls=3)["role"])
        state["controls"] = [{}, {}, {}]
        self.assertEqual("screening-candidate", MODULE.next_dispatch(state, screening_controls=3)["role"])

        qualification = {"status": "qualification", "selected_workers": 15, "qualification": []}
        first = MODULE.next_dispatch(qualification)
        self.assertEqual(("qualification-serial", "cold", 1), (first["role"], first["cache_state"], first["pair_id"]))
        qualification["qualification"].append({"role": "qualification-serial", "cache_state": "cold", "pair_id": 1})
        second = MODULE.next_dispatch(qualification)
        self.assertEqual(("qualification-candidate", 15), (second["role"], second["workers"]))
        qualification["qualification"].append({"role": "qualification-candidate", "cache_state": "cold", "pair_id": 1})
        third = MODULE.next_dispatch(qualification)
        self.assertEqual(("qualification-candidate", "cold", 2), (third["role"], third["cache_state"], third["pair_id"]))

    def test_identity_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.json"
            state.write_text(json.dumps({"source_sha": "c" * 40, "image_digest": "image@sha256:" + "d" * 64}))
            with self.assertRaises(ValueError):
                MODULE.load_state(state, "a" * 40, "image@sha256:" + "b" * 64)

    def test_unsafe_evidence_stops_upward_search(self) -> None:
        state = {"source_sha": "a" * 40, "image_digest": "image@sha256:" + "b" * 64, "probes": []}
        evidence = {"run_id": 7, "workers": 10, "source_sha": "a" * 40, "image_digest": "image@sha256:" + "b" * 64, "output_equivalent": True, "manifest_equivalent": True, "inventory_complete": True, "failures": 0, "ooms": 0, "evictions": 0, "restarts": 0, "memory_ratio": .76, "node_pressure": False, "cpu_throttled": False, "disk_saturated": False, "improvement": .2, "typescript_seconds": 8, "critical_path_seconds": 80}
        MODULE.record_evidence(state, evidence)
        self.assertEqual("screening-stopped-resource", state["status"])
        self.assertTrue(state["probes"][0]["safe"])
        self.assertEqual([0, 10], state["bracket"])

    def test_qualification_requires_complete_pairs_and_all_median_p95_gates(self) -> None:
        state = {"selected_workers": 15, "qualification": []}
        self.assertFalse(MODULE.evaluate_qualification(state)["promotable"])
        for cache_state in ("cold", "warm"):
            for pair_id in range(1, 6):
                state["qualification"].extend([
                    {"role": "qualification-serial", "cache_state": cache_state, "pair_id": pair_id, "safe": True, "typescript_seconds": 100 + pair_id, "critical_path_seconds": 200 + pair_id},
                    {"role": "qualification-candidate", "cache_state": cache_state, "pair_id": pair_id, "safe": True, "typescript_seconds": 70 + pair_id, "critical_path_seconds": 140 + pair_id},
                ])
        self.assertTrue(MODULE.evaluate_qualification(state)["promotable"])
        state["qualification"][1]["critical_path_seconds"] = 300
        self.assertFalse(MODULE.evaluate_qualification(state)["promotable"])

    def test_dispatch_uses_main_for_workflow_and_frozen_sha_for_checkout(self) -> None:
        source_sha = "a" * 40
        command = MODULE.dispatch_command(source_sha, {"experiment": "d16-parallel", "workers": 15, "cache_state": "warm", "pair_id": 2})
        self.assertEqual("main", command[command.index("--ref") + 1])
        self.assertIn(f"source_sha={source_sha}", command)
        self.assertIn("file_workers=15", command)


if __name__ == "__main__":
    unittest.main()
