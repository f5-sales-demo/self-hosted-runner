from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "ghcr.io/f5-sales-demo/self-hosted-runner@sha256:" + "a" * 64
REVISION = "b" * 40


class SbomPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.commands = self.directory / "commands.jsonl"
        self.sbom = self.directory / "sbom.json"
        self.environment = dict(os.environ)
        self.environment.update(
            PATH=str(self.directory) + os.pathsep + os.environ["PATH"],
            TEST_COMMANDS=str(self.commands),
            TEST_SBOM=str(self.sbom),
            TEST_GH_CODE="0",
            TEST_DOCKER_CODE="0",
        )
        program = """#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ["TEST_COMMANDS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
code = int(os.environ["TEST_GH_CODE" if name == "gh" else "TEST_DOCKER_CODE"])
if name == "docker" and code == 0:
    if "--format" not in sys.argv or sys.argv[sys.argv.index("--format") + 1] != "{{json .SBOM}}":
        print("documented single-platform SBOM format required", file=sys.stderr)
        sys.exit(19)
    sys.stdout.write(pathlib.Path(os.environ["TEST_SBOM"]).read_text())
sys.exit(code)
"""
        for name in ("gh", "docker"):
            executable = self.directory / name
            executable.write_text(program, encoding="utf-8")
            executable.chmod(0o700)
        self.valid = {
            "SPDX": {
                "spdxVersion": "SPDX-2.3",
                "SPDXID": "SPDXRef-DOCUMENT",
                "name": "synthetic-runner-image",
                "dataLicense": "CC0-1.0",
                "documentNamespace": "https://example.com/spdx/synthetic-runner",
                "creationInfo": {
                    "created": "2026-11-01T00:00:00Z",
                    "creators": ["Tool: syft"],
                },
                "packages": [
                    {"name": "synthetic-package", "SPDXID": "SPDXRef-package"}
                ],
            }
        }

    def run_promotion(
        self, payload: object, *, raw: bool = False
    ) -> subprocess.CompletedProcess[str]:
        self.sbom.write_text(
            str(payload) if raw else json.dumps(payload), encoding="utf-8"
        )
        return subprocess.run(
            ["bash", str(ROOT / "scripts/verify-promotion.sh"), IMAGE, REVISION],
            cwd=ROOT,
            env=self.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_rejected(self, payload: object, *, raw: bool = False) -> None:
        result = self.run_promotion(payload, raw=raw)
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_publisher_uses_catalogued_immutable_scanner(self) -> None:
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        scanner = catalog["base_images"].get("sbom_scanner", "")
        self.assertRegex(
            scanner, r"^docker\.io/docker/buildkit-syft-scanner@sha256:[0-9a-f]{64}$"
        )
        publish = (ROOT / ".github/workflows/publish.yml").read_text()
        self.assertIn(".base_images.sbom_scanner", publish)
        self.assertIn('--sbom="generator=${SBOM_SCANNER}"', publish)
        self.assertNotIn("actions/attest-sbom@", publish)

    def test_exact_image_and_source_are_checked_before_platform_sbom(self) -> None:
        result = self.run_promotion(self.valid)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.commands.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0:3], ["gh", "attestation", "verify"])
        self.assertIn("oci://" + IMAGE, calls[0])
        self.assertIn(REVISION, calls[0])
        self.assertIn("--deny-self-hosted-runners", calls[0])
        self.assertEqual(calls[1][0:4], ["docker", "buildx", "imagetools", "inspect"])
        self.assertEqual(calls[1][-1], IMAGE)
        self.assertEqual(calls[1][calls[1].index("--format") + 1], "{{json .SBOM}}")
        self.assertIn("SPDX SBOM", result.stdout)

    def test_failed_provenance_stops_before_sbom_inspection(self) -> None:
        self.environment["TEST_GH_CODE"] = "17"
        self.assert_rejected(self.valid)
        calls = [json.loads(line) for line in self.commands.read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "gh")

    def test_failed_sbom_inspection_is_rejected(self) -> None:
        self.environment["TEST_DOCKER_CODE"] = "18"
        self.assert_rejected(self.valid)

    def test_missing_sbom_is_rejected(self) -> None:
        self.assert_rejected({})

    def test_null_sbom_is_rejected(self) -> None:
        self.assert_rejected(None)

    def test_malformed_sbom_is_rejected(self) -> None:
        self.assert_rejected("broken-json", raw=True)

    def test_truncated_sbom_is_rejected(self) -> None:
        self.assert_rejected(json.dumps(self.valid)[:-5], raw=True)

    def test_empty_package_inventory_is_rejected(self) -> None:
        value = copy.deepcopy(self.valid)
        value["SPDX"]["packages"] = []
        self.assert_rejected(value)

    def test_unsupported_spdx_version_is_rejected(self) -> None:
        value = copy.deepcopy(self.valid)
        value["SPDX"]["spdxVersion"] = "SPDX-9.9"
        self.assert_rejected(value)

    def test_duplicate_package_identity_is_rejected(self) -> None:
        value = copy.deepcopy(self.valid)
        value["SPDX"]["packages"].append(copy.deepcopy(value["SPDX"]["packages"][0]))
        self.assert_rejected(value)

    def test_malformed_package_is_rejected(self) -> None:
        value = copy.deepcopy(self.valid)
        value["SPDX"]["packages"] = [{"name": "synthetic-package"}]
        self.assert_rejected(value)

    def test_missing_document_identity_is_rejected(self) -> None:
        value = copy.deepcopy(self.valid)
        del value["SPDX"]["SPDXID"]
        self.assert_rejected(value)

    def test_spdx_22_is_supported(self) -> None:
        value = copy.deepcopy(self.valid)
        value["SPDX"]["spdxVersion"] = "SPDX-2.2"
        result = self.run_promotion(value)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
