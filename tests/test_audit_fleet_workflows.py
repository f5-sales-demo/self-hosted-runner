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
            "      - uses: actions/setup-node@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "        with:", "          node-version: 22.23.2", "      - run: npm ci",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertFalse([item for item in findings if item.level == "error"])
        self.assertEqual([item.level for item in findings], ["info"])
        self.assertTrue(findings[0].location.startswith("f5-sales-demo/fixture/"))

    def test_static_environment_version_is_resolved(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "env:", "  NODE_VERSION: 22.23.2", "jobs:", "  verify:",
            "    runs-on: [self-hosted, Linux, X64, fixture, ubuntu-24.04]", "    steps:",
            "      - uses: actions/setup-node@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "        with:", "          node-version: ${{ env.NODE_VERSION }}",
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
            "      - uses: actions/setup-python@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "        with:", "          python-version: 3.12", "      - uses: vendor/setup-thing@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "      - run: sudo apt-get install jq",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertEqual(len([item for item in findings if item.level == "error"]), 3)

    def test_marketplace_action_requires_a_full_commit_sha(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "jobs:", "  verify:",
            "    runs-on: self-hosted", "    steps:",
            "      - uses: actions/checkout@v4",
        ])}
        findings = AUDIT.audit_workflows("f5-sales-demo/fixture", workflows, self.catalog["setup_actions"])
        self.assertIn("marketplace action must be pinned to a full commit SHA", findings[0].message)

    def test_inventory_classifies_actions_and_rejects_socket_on_standard(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "jobs:", "  verify:",
            "    runs-on: [self-hosted, Linux, X64, fixture, ubuntu-24.04]", "    steps:",
            "      - uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "      - uses: docker/login-action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ])}
        profiles = {tool["name"]: set(tool["profiles"]) for tool in self.catalog["tools"]}
        inventory = {}
        findings = AUDIT.audit_workflows(
            "f5-sales-demo/fixture", workflows, self.catalog["setup_actions"],
            self.catalog["marketplace_actions"], profiles, inventory,
        )
        self.assertTrue(any("docker-socket action is not allowed" in item.message for item in findings))
        result = {item["name"]: item for item in AUDIT.inventory_json(inventory)}
        self.assertEqual(result["actions/checkout"]["classification"], "runner-runtime-consumer")
        self.assertEqual(result["docker/login-action"]["profiles"], ["standard"])

    def test_unknown_marketplace_action_requires_a_dependency_classification(self) -> None:
        workflows = {".github/workflows/test.yml": workflow([
            "name: test", "on: push", "jobs:", "  verify:",
            "    runs-on: self-hosted", "    steps:",
            "      - uses: vendor/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ])}
        profiles = {tool["name"]: set(tool["profiles"]) for tool in self.catalog["tools"]}
        findings = AUDIT.audit_workflows(
            "f5-sales-demo/fixture", workflows, self.catalog["setup_actions"],
            self.catalog["marketplace_actions"], profiles, {},
        )
        self.assertTrue(any("has no dependency classification" in item.message for item in findings))


    def test_github_client_retries_only_transient_errors_with_pacing(self) -> None:
        calls, sleeps = [], []
        outcomes = [RuntimeError("HTTP 503"), "ok"]

        def run(args, *, timeout):
            calls.append((args, timeout))
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        client = AUDIT.GitHubClient(
            timeout=7,
            attempts=2,
            minimum_interval=3,
            command_fn=run,
            clock=lambda: 10,
            sleep_fn=sleeps.append,
        )
        self.assertEqual(client.request(["gh", "api", "example"]), "ok")
        self.assertEqual([item[1] for item in calls], [7, 7])
        self.assertEqual(sleeps, [3])
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            AUDIT.GitHubClient(
                command_fn=lambda _args, *, timeout: (_ for _ in ()).throw(
                    RuntimeError("HTTP 404")
                )
            ).request(["gh"])


if __name__ == "__main__":
    unittest.main()
