import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CanadaRunnerTests(unittest.TestCase):
    def test_capacity_and_dependency_inventory(self):
        for path in ["catalog/governed-repositories.json", "config/arc-capacity.json"]:
            self.assertIn("f5-sales-demo/canada-topology", json.loads((ROOT / path).read_text())["repositories"])
        repository = json.loads((ROOT / "arc/repositories/canada-topology.yaml").read_text())
        self.assertEqual(repository["repository"], "https://github.com/f5-sales-demo/canada-topology")
        self.assertEqual([(s["profile"], s["min_runners"], s["max_runners"]) for s in repository["scale_sets"]], [("socketless", 0, 3), ("container-build", 0, 1)])
        for scale in repository["scale_sets"]:
            self.assertTrue(scale["namespace"].startswith("arc-runners-canada-topology-"))
            self.assertTrue((ROOT / scale["values"]).is_file())


if __name__ == "__main__":
    unittest.main()
