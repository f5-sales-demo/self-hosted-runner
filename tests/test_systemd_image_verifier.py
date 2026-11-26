from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("verify_runner_tools", ROOT / "scripts/verify-tools.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class SystemdImageVerifierTests(unittest.TestCase):
    def verify(self, missing: bool):
        commands = []

        def command(value):
            commands.append(value)
            if value == "command -v docker":
                return 1, ""
            if "sd_listen_fds" in value and "-lsystemd" in value and missing:
                return 1, "ld: cannot find -lsystemd"
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            catalog.write_text(json.dumps({"tools": [], "setup_actions": {}}))
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(verifier.os, "geteuid", return_value=1001), \
                    mock.patch.object(verifier.os, "access", return_value=True), \
                    mock.patch.object(Path, "exists", return_value=False), \
                    mock.patch.object(Path, "is_file", return_value=True), \
                    mock.patch.object(Path, "is_symlink", return_value=False), \
                    mock.patch.object(verifier, "run", side_effect=command), \
                    mock.patch("sys.argv", ["verify-runner-tools", "standard", "--catalog", str(catalog)]), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = verifier.main()
        return code, commands, output.getvalue(), errors.getvalue()

    def test_missing_systemd_development_library_fails_image_verification(self):
        code, commands, _, errors = self.verify(missing=True)
        self.assertEqual(code, 1)
        self.assertIn("systemd development library", errors)
        self.assertTrue(any("sd_listen_fds" in value and "-lsystemd" in value for value in commands))

    def test_available_systemd_header_and_linkage_are_verified_without_a_service(self):
        code, commands, output, errors = self.verify(missing=False)
        self.assertEqual(code, 0, errors)
        self.assertTrue(any("systemd/sd-daemon.h" in value and "-lsystemd" in value for value in commands))
        self.assertIn("systemd development library", output)
        self.assertFalse(any("systemctl" in value for value in commands))

    def test_signed_snapshot_and_native_tool_contract_include_systemd(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        package_line = next(line for line in dockerfile.splitlines() if line.startswith("RUN packages="))
        self.assertIn("libsystemd-dev", package_line.split())
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        native = next(tool for tool in catalog["tools"] if tool["name"] == "xcsh-native-libraries")
        self.assertIn("libsystemd", native["command"].split())
