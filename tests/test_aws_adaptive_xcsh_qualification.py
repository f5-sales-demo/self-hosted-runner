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

    def test_schedules_serial_controls_before_screening_and_rotating_qualification_pairs(self) -> None:
        state = {"status": "screening", "controls": [], "probes": [], "qualification": []}
        self.assertEqual(
            {"role": "screening-control", "experiment": "d16-serial", "workers": 0, "cache_state": "warm", "pair_id": 1},
            MODULE.next_dispatch(state),
        )
        state["controls"] = [{}, {}, {}]
        self.assertEqual("screening-candidate", MODULE.next_dispatch(state)["role"])

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
        evidence = {"run_id": 7, "workers": 10, "source_sha": "a" * 40, "image_digest": "image@sha256:" + "b" * 64, "output_equivalent": True, "failures": 0, "ooms": 0, "evictions": 0, "restarts": 0, "memory_ratio": .76, "node_pressure": False, "cpu_throttled": False, "disk_saturated": False, "improvement": .2}
        MODULE.record_evidence(state, evidence)
        self.assertEqual("screening-stopped-resource", state["status"])
        self.assertTrue(state["probes"][0]["safe"])
        self.assertEqual([0, 10], state["bracket"])


if __name__ == "__main__":
    unittest.main()
