from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = "20261126T000000Z"
# Qualified from this snapshot's signed noble/noble-updates amd64 indexes.
JAVA_VERSIONS = {"java-17": "17.0.21.1", "java": "21.0.13.1"}
spec = importlib.util.spec_from_file_location("verify_java_image", ROOT / "scripts/verify-tools.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class JavaSnapshotContractTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        self.java = {tool["name"]: tool for tool in self.catalog["tools"] if tool["name"] in JAVA_VERSIONS}

    def verify(self, profile, tools, versions):
        commands = {tool["command"]: versions[tool["name"]] for tool in tools}

        def command(value):
            if value in commands:
                return 0, 'openjdk version "{}" 2026-11-17\nOpenJDK Runtime Environment\n'.format(commands[value])
            if value == "command -v docker":
                return (0, "/usr/bin/docker") if profile == "container-build" else (1, "")
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"tools": tools, "setup_actions": {}}))
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(verifier.os, "geteuid", return_value=1001), \
                    mock.patch.object(verifier.os, "access", return_value=True), \
                    mock.patch.object(Path, "exists", return_value=False), \
                    mock.patch.object(Path, "is_file", return_value=True), \
                    mock.patch.object(Path, "is_symlink", return_value=False), \
                    mock.patch.object(verifier, "run", side_effect=command), \
                    mock.patch("sys.argv", ["verify-runner-tools", profile, "--catalog", str(catalog)]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = verifier.main()
        return code, errors.getvalue()

    def test_catalog_snapshot_matches_actual_build_snapshot(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        snapshot = re.search(r"^ARG APT_SNAPSHOT=(\S+)$", dockerfile, re.MULTILINE).group(1)
        self.assertEqual(SNAPSHOT, snapshot)
        self.assertEqual(snapshot, self.catalog["base_images"]["apt_snapshot"])

    def test_java_17_catalog_matches_signed_package_runtime(self):
        self.assertEqual(JAVA_VERSIONS["java-17"], self.java["java-17"]["version"])
        self.assertIn(SNAPSHOT, self.java["java-17"]["source"])

    def test_java_21_catalog_matches_signed_package_runtime(self):
        self.assertEqual(JAVA_VERSIONS["java"], self.java["java"]["version"])
        self.assertIn(SNAPSHOT, self.java["java"]["source"])

    def test_setup_java_advertises_the_verified_installed_version(self):
        self.assertEqual([JAVA_VERSIONS["java-17"]], self.catalog["setup_actions"]["actions/setup-java"]["versions"])

    def test_signed_snapshot_runtime_outputs_pass_both_profiles(self):
        for profile in ("standard", "container-build"):
            with self.subTest(profile=profile):
                code, errors = self.verify(profile, list(self.java.values()), JAVA_VERSIONS)
                self.assertEqual(0, code, errors)

    def test_same_version_prefix_cannot_accept_a_different_patch(self):
        for profile in ("standard", "container-build"):
            for name, tool in self.java.items():
                with self.subTest(profile=profile, java=name):
                    code, errors = self.verify(profile, [tool], {name: tool["version"] + "0"})
                    self.assertEqual(1, code)
                    self.assertIn("[error] " + name + ":", errors)

    def test_a_different_java_major_is_rejected(self):
        for profile in ("standard", "container-build"):
            for name, tool in self.java.items():
                with self.subTest(profile=profile, java=name):
                    code, errors = self.verify(profile, [tool], {name: "25.0.1"})
                    self.assertEqual(1, code)
                    self.assertIn("[error] " + name + ":", errors)
