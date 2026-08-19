#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("fleet_audit", ROOT / "scripts/audit-fleet-workflows.py")
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def workflow(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


class FleetAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text(encoding="utf-8"))

    def test_manifest_contains_the_governed_fleet(self) -> None:
        fleet = json.loads((ROOT / "catalog/governed-repositories.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fleet["repositories"]), 39)
        self.assertIn("f5-sales-demo/docs-control", fleet["repositories"])

    def test_exact_cached_setup_and_lockfile_install_pass(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "jobs:", "  verify:",
            "    runs-on: [self-hosted, Linux, X64, fixture, ubuntu-24.04]", "    steps:",
            "      - uses: actions/setup-node@abc", "        with:", "          node-version: 22.23.2", "      - run: npm ci",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertFalse([item for item in findings if item.level == "error"])
        self.assertEqual([item.level for item in findings], ["info"])

    def test_static_environment_version_is_resolved(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "env:", "  NODE_VERSION: 22.23.2", "jobs:", "  verify:",
            "    runs-on: [self-hosted, Linux, X64, fixture, ubuntu-24.04]", "    steps:",
            "      - uses: actions/setup-node@abc", "        with:", "          node-version: ${{ env.NODE_VERSION }}",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertFalse([item for item in findings if item.level == "error"])

    def test_local_action_is_not_a_remote_tool_download(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "jobs:", "  verify:",
            "    runs-on: [self-hosted, Linux, X64, fixture, ubuntu-24.04]", "    steps:",
            "      - uses: ./.github/actions/download-api-specs",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertFalse([item for item in findings if item.level == "error"])

    def test_floating_and_direct_installers_fail(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "jobs:", "  verify:",
            "    runs-on: [self-hosted, Linux, X64, fixture, ubuntu-24.04]", "    steps:",
            "      - uses: actions/setup-python@abc", "        with:", "          python-version: 3.12", "      - uses: vendor/setup-thing@abc", "      - run: sudo apt-get install jq",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertEqual(len([item for item in findings if item.level == "error"]), 3)


if __name__ == "__main__":
    unittest.main()
