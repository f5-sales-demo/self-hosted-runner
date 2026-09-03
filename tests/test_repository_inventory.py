#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "repository_inventory", ROOT / "scripts/repository-inventory.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RepositoryInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in ("catalog", "arc/repositories", "config"):
            (self.root / relative).mkdir(parents=True)
        shutil.copy(ROOT / "catalog/governed-repositories.json", self.root / "catalog")
        shutil.copy(ROOT / "config/arc-capacity.json", self.root / "config")
        for source in (ROOT / "arc/repositories").glob("*.yaml"):
            shutil.copy(source, self.root / "arc/repositories")

    def tearDown(self):
        self.temporary.cleanup()

    def mutate_catalog(self, callback):
        path = self.root / "catalog/governed-repositories.json"
        value = json.loads(path.read_text())
        callback(value["repositories"])
        path.write_text(json.dumps(value))

    def test_exact_inventory_and_clean_break_config(self):
        repositories = MODULE.inventory(ROOT)
        self.assertEqual(39, len(repositories))
        config = MODULE.global_config(repositories)
        self.assertEqual("ignored", config["requireConfig"])
        self.assertFalse(config["onboarding"])
        self.assertEqual("7 days", config["minimumReleaseAge"])
        self.assertEqual("strict", config["internalChecksFilter"])
        self.assertEqual(
            {"prCreation": "immediate", "platformAutomerge": False},
            config["force"],
        )
        self.assertEqual(
            [
                r"^node scripts/prepare-generated-artifact-release\.mjs prepare$",
                r"^bash scripts/refresh-asm-migration-dependencies\.sh$",
            ],
            config["allowedCommands"],
        )
        self.assertFalse(
            any("schedule" in rule for rule in config["packageRules"]),
            "pre-release managers must be eligible on every production run",
        )
        groups = {rule.get("groupName"): rule for rule in config["packageRules"]}
        self.assertEqual({"minor", "patch"}, set(groups["npm-minor-patch"]["matchUpdateTypes"]))
        self.assertEqual({"minor", "patch"}, set(groups["actions-minor-patch"]["matchUpdateTypes"]))
        for group_name in ("npm-minor-patch", "actions-minor-patch"):
            rule = groups[group_name]
            self.assertTrue(rule["automerge"])
            self.assertEqual("pr", rule["automergeType"])
            self.assertFalse(
                rule["platformAutomerge"],
                "Renovate must observe passing CI before squash-merging",
            )
        self.assertNotIn("major", json.dumps(groups))
        npm_bump = next(
            rule
            for rule in config["packageRules"]
            if rule.get("description") == "Bump npm dependency range lower bounds"
        )
        self.assertEqual(["npm"], npm_bump["matchManagers"])
        self.assertEqual("bump", npm_bump["rangeStrategy"])
        self.assertNotIn("matchDepTypes", npm_bump)
        overrides = next(
            rule
            for rule in config["packageRules"]
            if rule.get("matchDepTypes") == ["overrides"]
        )
        self.assertEqual("pin", overrides["rangeStrategy"])
        self.assertNotIn("groupName", overrides)
        self.assertLess(
            config["packageRules"].index(npm_bump),
            config["packageRules"].index(overrides),
            "the later overrides rule must win over the general npm bump strategy",
        )
        task = next(
            rule
            for rule in config["packageRules"]
            if rule.get("matchRepositories") == ["f5-sales-demo/docs-icons"]
        )
        self.assertEqual(["f5-sales-demo/docs-icons"], task["matchRepositories"])
        self.assertEqual(["npm"], task["matchManagers"])
        self.assertEqual("always", task["recreateWhen"])
        self.assertEqual("branch", task["postUpgradeTasks"]["executionMode"])
        peer_pin = next(
            rule
            for rule in config["packageRules"]
            if rule.get("description")
            == "Preserve the vscode-xcsh Babel 7-compatible Rolldown plugin"
        )
        self.assertEqual(
            ["f5-sales-demo/vscode-xcsh"], peer_pin["matchRepositories"]
        )
        self.assertEqual(["npm"], peer_pin["matchManagers"])
        self.assertEqual(["@rolldown/plugin-babel"], peer_pin["matchPackageNames"])
        self.assertFalse(peer_pin["enabled"])
        uv_catalog = next(
            rule
            for rule in config["packageRules"]
            if rule.get("description")
            == "Keep docs-control uv input within the immutable runner catalog"
        )
        self.assertEqual(["f5-sales-demo/docs-control"], uv_catalog["matchRepositories"])
        self.assertEqual(["github-actions"], uv_catalog["matchManagers"])
        self.assertEqual(["astral-sh/uv"], uv_catalog["matchPackageNames"])
        self.assertEqual("0.8.24", uv_catalog["allowedVersions"])
        self.assertGreater(
            config["packageRules"].index(uv_catalog),
            config["packageRules"].index(groups["actions-minor-patch"]),
        )
        managed_workflows = next(
            rule
            for rule in config["packageRules"]
            if rule.get("description")
            == "Keep docs-control managed workflows canonical downstream"
        )
        self.assertEqual(
            sorted(set(repositories) - {"f5-sales-demo/docs-control"}),
            managed_workflows["matchRepositories"],
        )
        self.assertEqual(
            [
                ".github/workflows/antigravity-review.yml",
                ".github/workflows/auto-merge.yml",
                ".github/workflows/github-pages-deploy.yml",
                ".github/workflows/require-linked-issue.yml",
                ".github/workflows/semgrep.yml",
                ".github/workflows/super-linter.yml",
                ".github/workflows/translation-audit.yml",
                ".github/workflows/workflow-security-audit.yml",
            ],
            managed_workflows["matchFileNames"],
        )
        self.assertFalse(managed_workflows["enabled"])
        self.assertIs(
            managed_workflows,
            config["packageRules"][-1],
            "the managed-file exclusion must override every update rule downstream",
        )
        self.assertFalse(
            any("prCreation" in rule for rule in config["packageRules"]),
            "administrator force must remain the final effective override",
        )

    def test_rejects_duplicate_foreign_and_self_repository(self):
        for value in (
            "f5-sales-demo/administration",
            "other-org/repository",
            "f5-sales-demo/self-hosted-runner",
        ):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.InventoryError):
                    MODULE.validate_list(sorted([value] * 2), "test")
        with self.assertRaises(MODULE.InventoryError):
            MODULE.validate_list(["other-org/repository"], "test")
        with self.assertRaises(MODULE.InventoryError):
            MODULE.validate_list(["f5-sales-demo/self-hosted-runner"], "test")

    def test_rejects_missing_arc_and_capacity_disagreement(self):
        (self.root / "arc/repositories/docs-icons.yaml").unlink()
        with self.assertRaisesRegex(MODULE.InventoryError, "ARC must contain exactly"):
            MODULE.inventory(self.root)
        shutil.copy(
            ROOT / "arc/repositories/docs-icons.yaml",
            self.root / "arc/repositories/docs-icons.yaml",
        )
        capacity = self.root / "config/arc-capacity.json"
        value = json.loads(capacity.read_text())
        value["repositories"].remove("f5-sales-demo/docs-icons")
        capacity.write_text(json.dumps(value))
        with self.assertRaisesRegex(MODULE.InventoryError, "capacity inventory differs"):
            MODULE.inventory(self.root)

    def test_committed_output_is_deterministic(self):
        self.assertEqual(
            (ROOT / "renovate-system/generated/renovate.json").read_text(),
            MODULE.render(ROOT),
        )


if __name__ == "__main__":
    unittest.main()
