#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("arc_config", ROOT / "scripts/arc-config.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ArcConfigTests(unittest.TestCase):
    def test_repository_configs_are_exact(self) -> None:
        expected = {
            "self-hosted-runner.yaml": {
                "repository": "https://github.com/f5-sales-demo/self-hosted-runner",
                "socketless": ("arc-runners-socketless", "self-hosted-runner-socketless", 0, 20),
                "container-build": ("arc-runners-container-build", "self-hosted-runner-container-build", 0, 5),
            },
            "xcsh.yaml": {
                "repository": "https://github.com/f5-sales-demo/xcsh",
                "socketless": ("arc-runners-xcsh-socketless", "xcsh-socketless", 0, 10),
                "container-build": ("arc-runners-xcsh-container-build", "xcsh-container-build", 0, 3),
            },
        }
        for filename, contract in expected.items():
            with self.subTest(filename=filename):
                config = MODULE.load_config(ROOT / "arc/repositories" / filename, ROOT)
                self.assertEqual(contract["repository"], config["repository"])
                profiles = {item["profile"]: item for item in config["scale_sets"]}
                for profile in MODULE.PROFILES:
                    item = profiles[profile]
                    self.assertEqual(
                        contract[profile],
                        (
                            item["namespace"],
                            item["runner_scale_set_name"],
                            item["min_runners"],
                            item["max_runners"],
                        ),
                    )
                    self.assertEqual(f"arc/{profile}-values.yaml", item["values"])

    def test_profile_values_are_repository_neutral(self) -> None:
        for profile in MODULE.PROFILES:
            values = (ROOT / f"arc/{profile}-values.yaml").read_text(encoding="utf-8")
            for forbidden in ("runnerScaleSetName:", "minRunners:", "maxRunners:", "xcsh"):
                self.assertNotIn(forbidden, values)

    def test_malformed_configuration_fails_closed(self) -> None:
        baseline = MODULE.load_config(ROOT / "arc/repositories/xcsh.yaml", ROOT)
        mutations = []
        bad = copy.deepcopy(baseline); bad["repository"] = "git@github.com:f5-sales-demo/xcsh"; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["extra"] = True; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["scale_sets"][1]["profile"] = "socketless"; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["scale_sets"][1]["namespace"] = bad["scale_sets"][0]["namespace"]; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["scale_sets"][0]["release"] = "Not_DNS"; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["scale_sets"][0]["min_runners"] = -1; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["scale_sets"][0]["max_runners"] = 0; mutations.append(bad)
        bad = copy.deepcopy(baseline); bad["scale_sets"][0]["values"] = "../socketless-values.yaml"; mutations.append(bad)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for config in mutations:
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.subTest(config=config), self.assertRaises(MODULE.ConfigError):
                    MODULE.load_config(path, ROOT)

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"repository":"a","repository":"b","scale_sets":[]}', encoding="utf-8")
            with self.assertRaises(MODULE.ConfigError):
                MODULE.load_config(path, ROOT)


if __name__ == "__main__":
    unittest.main()
