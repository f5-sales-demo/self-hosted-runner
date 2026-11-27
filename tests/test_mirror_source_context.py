"""Exercise source credential scoping and immutable mirroring without registry calls."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = '{"schemaVersion":2}\n'
DIGEST = "sha256:" + hashlib.sha256(MANIFEST.encode()).hexdigest()
SOURCE = "ghcr.io/f5-sales-demo/self-hosted-runner@" + DIGEST
TARGET = "123456789012.dkr.ecr.us-east-1.amazonaws.com/self-hosted-runner"

MOCK = r"""#!/usr/bin/python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
config = Path(os.environ['DOCKER_CONFIG'])
row = {'tool': name, 'args': args, 'config': str(config)}
if (config / 'config.json').exists():
    row['configuration'] = json.loads((config / 'config.json').read_text())
with open(os.environ['EVENTS'], 'a') as log:
    log.write(json.dumps(row) + '\n')
if name == 'gh':
    if args == ['auth', 'token']: print('synthetic-fixture-access')
    elif args == ['api', 'user', '--jq', '.login']: print('fixture')
    else: sys.exit(95)
elif name == 'aws':
    if args[:2] == ['sts', 'get-caller-identity']:
        print(os.environ.get('FIXTURE_ACCOUNT', '123456789012'))
    elif args[:2] == ['ecr', 'get-login-password']: print('synthetic-fixture-target')
    elif args[:2] != ['ecr', 'describe-repositories']: sys.exit(95)
elif name == 'docker':
    if args[0] == 'login': sys.stdin.read()
    elif args[:4] == ['buildx', 'imagetools', 'inspect', '--raw']:
        is_source = args[4].startswith('ghcr.io/')
        if is_source and os.environ.get('SOURCE_ERROR'): sys.exit(42)
        wrong = os.environ.get('SOURCE_BAD') if is_source else os.environ.get('MIRROR_BAD')
        sys.stdout.write('wrong\n' if wrong else os.environ['MANIFEST'])
    elif args[:3] != ['buildx', 'imagetools', 'create']: sys.exit(95)
else: sys.exit(95)
"""


@contextmanager
def fixture_directory():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


class MirrorSourceContextTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(fixture_directory())
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        for name in ("gh", "docker", "aws"):
            path = self.bin / name
            path.write_text(MOCK)
            path.chmod(0o700)
        self.source_config = self.folder / "source"
        self.source_config.mkdir()
        self.source_config_file = self.source_config / "config.json"
        self.source_config_file.write_text(
            json.dumps(
                {
                    "auths": {
                        "ghcr.io": {"auth": "synthetic-ghcr-fixture"},
                        "unrelated.invalid": {"auth": "synthetic-unrelated-fixture"},
                    }
                }
            )
        )
        self.events = self.folder / "events"
        self.events.touch()
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + ":/usr/bin:/bin",
            EVENTS=str(self.events),
            MANIFEST=MANIFEST,
            AWS_ACCOUNT_ID="123456789012",
        )
        self.env.pop("GHCR_SOURCE_DOCKER_CONFIG", None)

    def run_mirror(self, configured=True, mode="copy", source=SOURCE):
        if configured:
            self.env["GHCR_SOURCE_DOCKER_CONFIG"] = str(self.source_config)
        before = self.source_config_file.read_bytes()
        result = subprocess.run(
            [
                "/bin/bash",
                str(ROOT / "scripts/mirror-runner-image.sh"),
                mode,
                source,
                TARGET if mode == "copy" else TARGET + "@" + DIGEST,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(before, self.source_config_file.read_bytes())
        self.calls = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertTrue(all(not Path(row["config"]).exists() for row in self.calls))
        return result

    def copies(self):
        return [
            r for r in self.calls if r["args"][:3] == ["buildx", "imagetools", "create"]
        ]

    def test_configured_ghcr_access_excludes_unrelated_credentials(self):
        result = self.run_mirror()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(TARGET + "@" + DIGEST, result.stdout.strip())
        self.assertFalse(any(r["tool"] == "gh" for r in self.calls))
        source = next(
            r
            for r in self.calls
            if r["args"][:4] == ["buildx", "imagetools", "inspect", "--raw"]
        )
        self.assertEqual(
            {"auths": {"ghcr.io": {"auth": "synthetic-ghcr-fixture"}}},
            source["configuration"],
        )
        self.assertEqual(1, len(self.copies()))

    def test_configured_helper_is_scoped_only_to_ghcr(self):
        self.source_config_file.write_text(
            json.dumps(
                {
                    "credsStore": "fixture-store",
                    "credHelpers": {"unrelated.invalid": "other"},
                }
            )
        )
        result = self.run_mirror()
        self.assertEqual(0, result.returncode, result.stderr)
        source = next(
            r
            for r in self.calls
            if r["args"][:4] == ["buildx", "imagetools", "inspect", "--raw"]
        )
        self.assertEqual(
            {"auths": {}, "credHelpers": {"ghcr.io": "fixture-store"}},
            source["configuration"],
        )
        self.assertFalse(any(r["tool"] == "gh" for r in self.calls))

    def test_default_ghcr_authentication_remains_explicit(self):
        result = self.run_mirror(configured=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(
            any(
                r["tool"] == "gh" and r["args"] == ["auth", "token"] for r in self.calls
            )
        )

    def test_missing_configured_credentials_fail_before_copy(self):
        self.source_config_file.write_text("{}")
        result = self.run_mirror()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.copies())
        self.assertFalse(any(r["tool"] == "gh" for r in self.calls))

    def test_malformed_configured_credentials_fail_before_copy(self):
        self.source_config_file.write_text("{malformed")
        result = self.run_mirror()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.copies())

    def test_multiple_configured_documents_fail_at_input_boundary(self):
        payload = self.source_config_file.read_text()
        self.source_config_file.write_text(payload + "\n" + payload)
        result = self.run_mirror()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.copies())
        self.assertIn("configured GHCR source credentials are invalid", result.stderr)

    def test_source_manifest_mismatch_prevents_copy(self):
        self.env["SOURCE_BAD"] = "1"
        result = self.run_mirror(configured=False)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.copies())

    def test_source_authentication_failure_prevents_copy(self):
        self.env["SOURCE_ERROR"] = "1"
        result = self.run_mirror(configured=False)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.copies())

    def test_foreign_target_account_prevents_copy(self):
        self.env["FIXTURE_ACCOUNT"] = "999999999999"
        result = self.run_mirror()
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.copies())

    def test_mirror_mismatch_never_reports_success(self):
        self.env["MIRROR_BAD"] = "1"
        result = self.run_mirror()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)

    def test_verify_mode_never_submits_copy(self):
        result = self.run_mirror(mode="verify")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.copies())


if __name__ == "__main__":
    unittest.main()
