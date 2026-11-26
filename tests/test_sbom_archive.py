from __future__ import annotations

import hashlib
import io
import json
import subprocess
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
