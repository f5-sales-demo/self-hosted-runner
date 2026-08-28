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
                "compute": (
                    "arc-runners-xcsh-compute",
                    "xcsh-compute",
                    0,
                    5,
                ),
            },
        }
        for filename, contract in expected.items():
            with self.subTest(filename=filename):
                config = MODULE.load_config(ROOT / "arc/repositories" / filename, ROOT)
                self.assertEqual(contract["repository"], config["repository"])
                profiles = {item["profile"]: item for item in config["scale_sets"]}
                expected_profiles = set(contract) - {"repository"}
                self.assertEqual(expected_profiles, set(profiles))
                for profile in expected_profiles:
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

    def test_managed_repository_configs_are_exact(self) -> None:
        expected = {
            "docs-control": (8, 2),
            "api-specs": (6, 2),
            "api-specs-enriched": (6, 2),
            "terraform-provider-xcsh": (6, 2),
            "devcontainer": (4, 2),
            "console": (4, 1),
            "marketplace": (4, 1),
            "marketplace-claude-code": (4, 1),
            "mcn": (4, 1),
            "origin-server": (4, 1),
            "starlight-mega-menu": (4, 1),
            "vscode-xcsh": (4, 1),
            "xcsh-action": (4, 1),
            "xcsh-chrome-extension": (4, 1),
            "administration": (3, 1),
            "api-protection": (3, 1),
            "apt-repo": (3, 1),
            "bot-advanced": (3, 1),
            "bot-standard": (3, 1),
            "cdn": (3, 1),
            "cdn-simulator": (3, 1),
            "csd": (3, 1),
            "ddos": (3, 1),
            "demo-resource-template": (3, 1),
            "demo-resources": (3, 1),
            "dns": (3, 1),
            "nginx": (3, 1),
            "observability": (3, 1),
            "traffic-generator": (3, 1),
            "waf": (3, 1),
            "was": (3, 1),
            "webapp-api-protection": (3, 1),
        }
        self.assertEqual(32, len(expected))
        self.assertEqual(
            {f"https://github.com/f5-sales-demo/{name}" for name in expected},
            MODULE.MANAGED_COHORT,
        )
        for repository, (socketless_max, container_max) in expected.items():
            with self.subTest(repository=repository):
                config = MODULE.load_config(CONFIG_DIR / f"{repository}.yaml", ROOT)
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
                    self.assertEqual(
                        f"managed-{profile}", item["runner_scale_set_name"]
                    )
                    self.assertEqual(0, item["min_runners"])
                    self.assertEqual(maximum, item["max_runners"])

    def test_config_directory_exactly_covers_catalog_plus_controller_repo(self) -> None:
        catalog = json.loads(
            (ROOT / "catalog/governed-repositories.json").read_text(encoding="utf-8")
        )
        expected = {
            repository.split("/", 1)[1] for repository in catalog["repositories"]
        }
        expected.add("self-hosted-runner")
        self.assertEqual(expected, {path.stem for path in CONFIG_DIR.glob("*.yaml")})
        self.assertEqual(40, len(expected))

    def test_all_configs_have_globally_safe_identities(self) -> None:
        paths = sorted(CONFIG_DIR.glob("*.yaml"))
        configs = MODULE.validate_complete_config_set(paths, ROOT)
        self.assertEqual(len(paths), len(configs))
        docs = [
            config for config in configs if config["repository"] in MODULE.DOCS_COHORT
        ]
        self.assertEqual(6, len(docs))
        managed = [
            config
            for config in configs
            if config["repository"] in MODULE.MANAGED_COHORT
        ]
        self.assertEqual(32, len(managed))
        self.assertEqual(40, len(configs))

    def test_complete_config_set_rejects_missing_repository(self) -> None:
        paths = sorted(CONFIG_DIR.glob("*.yaml"))
        with self.assertRaisesRegex(MODULE.ConfigError, "coverage mismatch"):
            MODULE.validate_complete_config_set(paths[:-1], ROOT)

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

    def test_shared_labels_cannot_escape_or_swap(self) -> None:
        escaped_docs = MODULE.load_config(CONFIG_DIR / "xcsh.yaml", ROOT)
        escaped_docs["scale_sets"][0]["runner_scale_set_name"] = "docs-socketless"
        escaped_managed = MODULE.load_config(CONFIG_DIR / "xcsh.yaml", ROOT)
        escaped_managed["scale_sets"][0]["runner_scale_set_name"] = "managed-socketless"
        swapped_docs = MODULE.load_config(CONFIG_DIR / "docs.yaml", ROOT)
        swapped_docs["scale_sets"][0]["runner_scale_set_name"] = "docs-container-build"
        swapped_docs["scale_sets"][1]["runner_scale_set_name"] = "docs-socketless"
        swapped_managed = MODULE.load_config(CONFIG_DIR / "administration.yaml", ROOT)
        swapped_managed["scale_sets"][0]["runner_scale_set_name"] = (
            "managed-container-build"
        )
        swapped_managed["scale_sets"][1]["runner_scale_set_name"] = "managed-socketless"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for config in (
                escaped_docs,
                escaped_managed,
                swapped_docs,
                swapped_managed,
            ):
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
        bad["scale_sets"][0]["max_runners"] = 9
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

    def test_compute_profile_is_xcsh_only_and_socketless(self) -> None:
        xcsh = MODULE.load_config(CONFIG_DIR / "xcsh.yaml", ROOT)
        compute = next(
            item for item in xcsh["scale_sets"] if item["profile"] == "compute"
        )
        self.assertEqual("arc-runners-xcsh-compute", compute["namespace"])
        values = (ROOT / compute["values"]).read_text(encoding="utf-8")
        self.assertIn("runner-profile: compute", values)
        self.assertIn('cpu: "14"', values)
        self.assertIn("memory: 48Gi", values)
        self.assertIn('cpu: "15"', values)
        self.assertIn("memory: 56Gi", values)
        self.assertNotIn("privileged: true", values)
        self.assertNotIn("DOCKER_HOST", values)

        unapproved = MODULE.load_config(CONFIG_DIR / "docs.yaml", ROOT)
        unapproved["scale_sets"].append(copy.deepcopy(compute))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(unapproved), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ConfigError, "approved only"):
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
