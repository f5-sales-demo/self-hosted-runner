#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "arc_config", ROOT / "scripts/arc-config.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


CONFIG_DIR = ROOT / "arc/repositories"


class ArcConfigTests(unittest.TestCase):
    def test_repository_configs_are_exact(self) -> None:
        expected = {
            "self-hosted-runner.yaml": {
                "repository": "https://github.com/f5-sales-demo/self-hosted-runner",
                "socketless": (
                    "arc-runners-socketless",
                    "self-hosted-runner-socketless",
                    0,
                    20,
                ),
                "container-build": (
                    "arc-runners-container-build",
                    "self-hosted-runner-container-build",
                    0,
                    5,
                ),
            },
            "xcsh.yaml": {
                "repository": "https://github.com/f5-sales-demo/xcsh",
                "socketless": ("arc-runners-xcsh-socketless", "xcsh-socketless", 0, 10),
                "container-build": (
                    "arc-runners-xcsh-container-build",
                    "xcsh-container-build",
                    0,
                    3,
                ),
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

    def test_docs_repository_configs_are_exact(self) -> None:
        expected = {
            "docs": (3, 1),
            "docs-builder": (4, 2),
            "docs-theme": (3, 1),
            "i18n-core": (3, 1),
            "starlight-llms-txt": (3, 1),
            "docs-icons": (3, 1),
        }
        for repository, (socketless_max, container_max) in expected.items():
            with self.subTest(repository=repository):
                config = MODULE.load_config(CONFIG_DIR / f"{repository}.yaml", ROOT)
                self.assertEqual(
                    f"https://github.com/f5-sales-demo/{repository}",
                    config["repository"],
                )
                profiles = {item["profile"]: item for item in config["scale_sets"]}
                for profile, maximum in (
                    ("socketless", socketless_max),
                    ("container-build", container_max),
                ):
                    item = profiles[profile]
                    self.assertEqual(
                        f"arc-runners-{repository}-{profile}", item["namespace"]
                    )
                    self.assertEqual(f"{repository}-{profile}", item["release"])
                    self.assertEqual(f"docs-{profile}", item["runner_scale_set_name"])
                    self.assertEqual(0, item["min_runners"])
                    self.assertEqual(maximum, item["max_runners"])

    def test_all_configs_have_globally_safe_identities(self) -> None:
        paths = sorted(CONFIG_DIR.glob("*.yaml"))
        configs = MODULE.validate_config_set(paths, ROOT)
        self.assertEqual(len(paths), len(configs))
        docs = [
            config for config in configs if config["repository"] in MODULE.DOCS_COHORT
        ]
        self.assertEqual(6, len(docs))

    def test_cross_config_collisions_fail_closed(self) -> None:
        first = MODULE.load_config(CONFIG_DIR / "self-hosted-runner.yaml", ROOT)
        second = MODULE.load_config(CONFIG_DIR / "xcsh.yaml", ROOT)
        mutations = []
        for field in ("namespace", "release", "runner_scale_set_name"):
            bad = copy.deepcopy(second)
            bad["scale_sets"][0][field] = first["scale_sets"][0][field]
            mutations.append(bad)
        duplicate_repository = copy.deepcopy(second)
        duplicate_repository["repository"] = first["repository"]
        mutations.append(duplicate_repository)
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.json"
            second_path = Path(directory) / "second.json"
            first_path.write_text(json.dumps(first), encoding="utf-8")
            for config in mutations:
                second_path.write_text(json.dumps(config), encoding="utf-8")
                with self.subTest(config=config), self.assertRaises(MODULE.ConfigError):
                    MODULE.validate_config_set([first_path, second_path], ROOT)

    def test_docs_shared_labels_cannot_escape_or_swap(self) -> None:
        escaped = MODULE.load_config(CONFIG_DIR / "xcsh.yaml", ROOT)
        escaped["scale_sets"][0]["runner_scale_set_name"] = "docs-socketless"
        swapped = MODULE.load_config(CONFIG_DIR / "docs.yaml", ROOT)
        swapped["scale_sets"][0]["runner_scale_set_name"] = "docs-container-build"
        swapped["scale_sets"][1]["runner_scale_set_name"] = "docs-socketless"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for config in (escaped, swapped):
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.subTest(config=config), self.assertRaises(MODULE.ConfigError):
                    MODULE.load_config(path, ROOT)

    def test_profile_values_are_repository_neutral(self) -> None:
        for profile in MODULE.PROFILES:
            values = (ROOT / f"arc/{profile}-values.yaml").read_text(encoding="utf-8")
            for forbidden in (
                "runnerScaleSetName:",
                "minRunners:",
                "maxRunners:",
                "xcsh",
            ):
                self.assertNotIn(forbidden, values)

    def test_malformed_configuration_fails_closed(self) -> None:
        baseline = MODULE.load_config(ROOT / "arc/repositories/xcsh.yaml", ROOT)
        mutations = []
        bad = copy.deepcopy(baseline)
        bad["repository"] = "git@github.com:f5-sales-demo/xcsh"
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["extra"] = True
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["scale_sets"][1]["profile"] = "socketless"
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["scale_sets"][1]["namespace"] = bad["scale_sets"][0]["namespace"]
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["scale_sets"][0]["release"] = "Not_DNS"
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["scale_sets"][0]["min_runners"] = -1
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["scale_sets"][0]["max_runners"] = 0
        mutations.append(bad)
        bad = copy.deepcopy(baseline)
        bad["scale_sets"][0]["values"] = "../socketless-values.yaml"
        mutations.append(bad)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for config in mutations:
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.subTest(config=config), self.assertRaises(MODULE.ConfigError):
                    MODULE.load_config(path, ROOT)

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(
                '{"repository":"a","repository":"b","scale_sets":[]}', encoding="utf-8"
            )
            with self.assertRaises(MODULE.ConfigError):
                MODULE.load_config(path, ROOT)


if __name__ == "__main__":
    unittest.main()
