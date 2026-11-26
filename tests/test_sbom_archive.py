from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SbomArchiveTests(unittest.TestCase):
    def archive(self, *, missing: bool = False, foreign: bool = False, duplicate: bool = False, corrupt: bool = False, architecture: str = "amd64") -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        archive = Path(temporary.name) / "probe.oci.tar"
        members: dict[str, bytes] = {}

        def blob(value: object) -> dict[str, object]:
            data = json.dumps(value).encode()
            digest = hashlib.sha256(data).hexdigest()
            members["blobs/sha256/" + digest] = data
            return {"digest": "sha256:" + digest, "size": len(data)}

        image = blob({"schemaVersion": 2, "layers": []})
        image["platform"] = {"os": "linux", "architecture": architecture}
        document = {
            "spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT",
            "name": "synthetic-probe", "dataLicense": "CC0-1.0",
            "documentNamespace": "https://example.com/spdx/synthetic-probe",
            "creationInfo": {"created": "2026-11-01T00:00:00Z", "creators": ["Tool: syft"]},
            "packages": [{"name": "synthetic-package", "SPDXID": "SPDXRef-package"}],
        }
        statement = blob({
            "predicateType": "https://spdx.dev/Document",
            "subject": [{"name": "synthetic-probe", "digest": {"sha256": "f" * 64 if foreign else str(image["digest"]).removeprefix("sha256:")}}],
            "predicate": document,
        })
        statement["mediaType"] = "application/vnd.in-toto+json"
        attestation = blob({"schemaVersion": 2, "layers": [] if missing else [statement]})
        attestation["annotations"] = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": image["digest"],
        }
        manifests = [image, attestation]
        if duplicate:
            second = dict(attestation)
            manifests.append(second)
        members["index.json"] = json.dumps({"schemaVersion": 2, "manifests": manifests}).encode()
        if corrupt:
            members["blobs/sha256/" + str(statement["digest"]).removeprefix("sha256:")] += b" "
        with tarfile.open(archive, "w") as stream:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
        return archive

    def verify(self, **options: object) -> subprocess.CompletedProcess[str]:
        archive = self.archive(**options)
        return subprocess.run(
            ["python3", str(ROOT / "scripts/verify-sbom.py"), "--oci-archive", str(archive)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_attributed_nonempty_spdx_archive_is_verified(self) -> None:
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sourceManifest=sha256:", result.stdout)
        self.assertIn("packages=1", result.stdout)

    def test_missing_archive_sbom_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(missing=True).returncode, 0)

    def test_foreign_subject_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(foreign=True).returncode, 0)

    def test_duplicate_attribution_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(duplicate=True).returncode, 0)

    def test_corrupted_attestation_blob_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(corrupt=True).returncode, 0)

    def test_wrong_platform_is_rejected(self) -> None:
        self.assertNotEqual(self.verify(architecture="arm64").returncode, 0)

    def test_cli_identifies_attribution_failure_without_dumping_payload(self) -> None:
        result = self.verify(architecture="arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or ambiguous Linux/amd64 image", result.stderr)
        self.assertNotIn("synthetic-package", result.stderr)

    def test_cli_identifies_foreign_subject_without_dumping_payload(self) -> None:
        result = self.verify(foreign=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("foreign SPDX statement subject", result.stderr)
        self.assertNotIn("synthetic-package", result.stderr)

    def test_hosted_probe_names_oci_output_and_retains_subject_diagnostics(self) -> None:
        workflow = (ROOT / ".github/workflows/verify-sbom-export.yml").read_text()
        self.assertIn('--tag "$probe_reference"', workflow)
        self.assertIn("probe_reference=", workflow)
        self.assertIn("--attribution-report", workflow)
        self.assertIn("/attribution.json", workflow)

    def test_cli_retains_foreign_subject_structure_without_spdx_payload(self) -> None:
        archive = self.archive(foreign=True)
        report = archive.parent / "attribution.json"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/verify-sbom.py"),
             "--oci-archive", str(archive), "--attribution-report", str(report)],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(report.is_file(), result.stderr)
        value = json.loads(report.read_text())
        self.assertEqual(value["statements"][0]["subjectCount"], 1)
        self.assertEqual(value["statements"][0]["subjectSha256"], ["f" * 64])
        self.assertNotIn("synthetic-package", report.read_text())
        self.assertNotIn("synthetic-probe", report.read_text())
        self.assertIn("foreign SPDX statement subject", result.stderr)

    def test_cli_retains_valid_subject_binding_with_success(self) -> None:
        archive = self.archive()
        report = archive.parent / "attribution.json"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/verify-sbom.py"),
             "--oci-archive", str(archive), "--attribution-report", str(report)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(report.read_text())
        self.assertEqual(value["statements"][0]["subjectCount"], 1)
        self.assertEqual(value["statements"][0]["subjectSha256"],
                         [value["imageManifests"][0].removeprefix("sha256:")])
        self.assertTrue(value["inspectionComplete"])

    def test_hosted_probe_exports_registry_candidate_without_publishing(self) -> None:
        workflow = (ROOT / ".github/workflows/verify-sbom-export.yml").read_text()
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("attestations: read", workflow)
        self.assertIn("--output", workflow)
        self.assertIn("type=oci", workflow)
        self.assertIn("--oci-archive", workflow)
        self.assertIn("--sbom=", workflow)
        self.assertNotIn("--push", workflow)


if __name__ == "__main__":
    unittest.main()
