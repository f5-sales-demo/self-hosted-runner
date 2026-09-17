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


if __name__ == "__main__":
    unittest.main()
