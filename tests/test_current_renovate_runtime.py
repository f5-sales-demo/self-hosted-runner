#!/usr/bin/env python3
"""Exercise reviewed runtime inputs before any image or cluster submission."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CurrentRenovateRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "renovate-system").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.trace = self.root / "trace"
        self.source = json.loads(
            (ROOT / "renovate-system/image-source.json").read_text()
        )
        self.source["runtime"] = {"node": "26.13.1", "helm": "4.3.2"}
        for name in ["verify-renovate-runtime.sh", "renovate-deploy.sh"]:
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "TRACE": str(self.trace),
            "FIXTURE_VERSION": "45.1.1",
            "FIXTURE_HELM": "v4.3.2+g8346aaa",
        }
        self.stub(
            "docker",
            r"""#!/usr/bin/env python3
import os, pathlib, sys
with open(os.environ['TRACE'], 'a') as f:
    f.write('docker ' + repr(sys.argv[1:]) + '\n')
if sys.argv[-1] == '--version':
    for i, arg in enumerate(sys.argv):
        if arg == '--volume':
            pathlib.Path(sys.argv[i + 1].split(':')[0], 'installation-token').unlink()
    print(os.environ['FIXTURE_VERSION'])
else:
    if sys.argv[-1] != '26.13.1' or 'install-tool node "$1"' not in sys.argv[-3]:
        raise SystemExit(47)
    print('Install tool node succeeded')
""",
        )
        self.stub("helm", "#!/bin/sh\nprintf '%s\\n' \"$FIXTURE_HELM\"\n")
        self.stub("git", "#!/bin/sh\nexit 0\n")
        self.stub("kubectl", "#!/bin/sh\nexit 48\n")
        self.stub(
            "gh", "#!/bin/sh\nprintf 'attestation reached\\n' >>\"$TRACE\"\nexit 29\n"
        )
        config = self.root / "kubeconfig"
        config.write_text("fixture only\n")
        config.chmod(0o600)
        self.env["KUBECONFIG"] = str(config)
        self.env["SOCKETLESS_IMAGE"] = (
            "ghcr.io/f5-sales-demo/self-hosted-runner@sha256:" + "2" * 64
        )

    def stub(self, name, source):
        p = self.bin / name
        p.write_text(source)
        p.chmod(0o755)

    def save(self):
        (self.root / "renovate-system/image-source.json").write_text(
            json.dumps(self.source)
        )
        lock = {
            "schema_version": 1,
            "upstream": self.source["upstream"],
            "derived": {
                "acr": "f5salesdemoarcca.azurecr.io/renovate@sha256:" + "1" * 64,
                "ghcr": "ghcr.io/f5-sales-demo/renovate@sha256:" + "1" * 64,
                "source_commit": "a" * 40,
                "manifest_receipt": "sha256:" + "1" * 64,
            },
        }
        (self.root / "renovate-system/image-lock.json").write_text(json.dumps(lock))

    def run_script(self, name):
        self.save()
        arg = (
            "local/renovate:test"
            if name.startswith("verify")
            else "renovate-system/image-lock.json"
        )
        return subprocess.run(
            ["bash", str(self.root / "scripts" / name), arg],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_current_stable_image_receipt_matches_dockerfile(self):
        source = json.loads((ROOT / "renovate-system/image-source.json").read_text())
        self.assertEqual("45.1.1", source["upstream"]["version"])
        self.assertEqual({"node": "26.13.1", "helm": "4.3.2"}, source["runtime"])
        image = source["upstream"]["image"].replace(
            "renovate/renovate@", "renovate/renovate:45.1.1@"
        )
        self.assertIn(image, (ROOT / "renovate-system/Dockerfile").read_text())

    def test_runtime_version_and_tool_provision_follow_reviewed_receipt(self):
        self.source["upstream"]["version"] = "45.1.1"
        result = self.run_script("verify-renovate-runtime.sh")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(2, self.trace.read_text().count("docker "))

    def test_runtime_version_is_an_exact_line(self):
        self.source["upstream"]["version"] = "45.1.1"
        for output in ["45.1.10", "prefix 45.1.1", "45.1.1 suffix"]:
            with self.subTest(output=output):
                self.trace.unlink(missing_ok=True)
                self.env["FIXTURE_VERSION"] = output
                result = self.run_script("verify-renovate-runtime.sh")
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(1, self.trace.read_text().count("docker "))

    def test_changed_reviewed_version_rejects_old_image(self):
        self.source["upstream"]["version"] = "45.2.0"
        result = self.run_script("verify-renovate-runtime.sh")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(1, self.trace.read_text().count("docker "))

    def test_malformed_versions_fail_before_docker(self):
        for section, key in [("upstream", "version"), ("runtime", "node")]:
            for value in [
                "45.1.1;touch injected",
                "$(touch injected)",
                "045.1.1",
                "45.1",
                None,
                45,
            ]:
                with self.subTest(section=section, key=key, value=value):
                    self.trace.unlink(missing_ok=True)
                    original = self.source[section][key]
                    self.source[section][key] = value
                    result = self.run_script("verify-renovate-runtime.sh")
                    self.source[section][key] = original
                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse(self.trace.exists(), result.stdout + result.stderr)
                    self.assertFalse((self.root / "injected").exists())

    def test_current_helm_reaches_receipt_attestation_without_submission(self):
        result = self.run_script("renovate-deploy.sh")
        self.assertEqual(29, result.returncode, result.stderr)
        self.assertEqual("attestation reached\n", self.trace.read_text())

    def test_wrong_or_malformed_helm_fails_before_attestation(self):
        for value in ["v3.21.3+g1ad6e68", "v4.3.20+g8346aaa", "v4.3.2-rc.1+g8346aaa"]:
            with self.subTest(value=value):
                self.trace.unlink(missing_ok=True)
                self.env["FIXTURE_HELM"] = value
                result = self.run_script("renovate-deploy.sh")
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(self.trace.exists())
        self.env["FIXTURE_HELM"] = "v4.3.2+g8346aaa"
        self.source["runtime"]["helm"] = "$(touch injected)"
        result = self.run_script("renovate-deploy.sh")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.trace.exists())
        self.assertFalse((self.root / "injected").exists())


if __name__ == "__main__":
    unittest.main()
