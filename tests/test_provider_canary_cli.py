"""Exercise the actual deployment CLI without a live cluster or registry."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "compute-toolchain-canary"
NAMESPACE = "arc-runners-terraform-provider-xcsh-" + PROFILE
RELEASE = "terraform-provider-xcsh-" + PROFILE
IMAGE = "ghcr.io/f5-sales-demo/self-hosted-runner@sha256:" + "a" * 64
SOURCE = "b" * 40

# These fixtures expose argv, not a replacement deployment implementation.
COMMAND_FIXTURE = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
entry = {"command": name, "args": args}
if name == "helm" and args and args[0] == "upgrade":
    entry["values"] = Path(args[args.index("--values") + 1]).read_text()
with open(os.environ["COMMAND_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(entry) + "\n")
if name == "gh":
    if args[:2] == ["auth", "status"]:
        sys.exit(1)
    if args[:2] == ["attestation", "verify"]:
        sys.exit(int(os.environ.get("PROVENANCE_EXIT", "0")))
    sys.exit(97)
if name == "docker":
    if args[:3] != ["buildx", "imagetools", "inspect"] or args[-1] != os.environ["TOOLCHAIN_CANARY_IMAGE"]:
        sys.exit(97)
    code = int(os.environ.get("SBOM_EXIT", "0"))
    if code:
        sys.exit(code)
    print(json.dumps({"SPDX": {
        "spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT",
        "name": "synthetic-canary", "dataLicense": "CC0-1.0",
        "documentNamespace": "https://example.com/spdx/synthetic-canary",
        "creationInfo": {"created": "2026-11-01T00:00:00Z", "creators": ["Tool: syft"]},
        "packages": [{"name": "synthetic-package", "SPDXID": "SPDXRef-package"}]
    }}))
    sys.exit(0)
if name == "helm":
    if args == ["version", "--short"]:
        print("v3.21.3+g1ad6e68")
    elif args and args[0] == "pull":
        print("Digest: sha256:579e3a1bdf4032b3c3de3e9b0880a4a6d3c1989a67c06010f680c1cc49524d11")
    elif not args or args[0] != "upgrade":
        sys.exit(97)
if name == "kubectl":
    if args[:2] == ["auth", "can-i"]:
        print("yes")
    elif args[:2] == ["get", "secret"]:
        pass
    elif args[:2] == ["get", "autoscalinglisteners,ephemeralrunnersets"]:
        print(json.dumps({"items": [
            {"kind": "AutoscalingListener", "metadata": {"name": "canary-listener", "namespace": "arc-systems", "uid": "fixture-uid"}, "spec": {"autoscalingRunnerSetNamespace": os.environ["RUNNER_NAMESPACE"], "ephemeralRunnerSetName": "canary-set"}},
            {"kind": "EphemeralRunnerSet", "metadata": {"namespace": os.environ["RUNNER_NAMESPACE"], "name": "canary-set"}}
        ]}))
    elif args[:2] == ["get", "pods"]:
        print(json.dumps({"items": [{"metadata": {"name": "canary-listener"}, "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": [{"restartCount": 0}]}}]}))
    else:
        sys.exit(97)
"""


class ProviderCanaryCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("arc", "config", "terraform", "catalog", "scripts"):
            shutil.copytree(ROOT / directory, self.root / directory)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("gh", "docker", "helm", "kubectl"):
            command = self.bin / name
            command.write_text(COMMAND_FIXTURE)
            command.chmod(0o700)
        self.log = self.root / "commands.jsonl"
        config = self.root / "kubeconfig"
        config.write_text("offline fixture only\n")
        config.chmod(0o600)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "KUBECONFIG": str(config),
            "COMMAND_LOG": str(self.log),
            "RUNNER_NAMESPACE": NAMESPACE,
            "TOOLCHAIN_CANARY_IMAGE": IMAGE,
            "TOOLCHAIN_CANARY_SOURCE_SHA": SOURCE,
            "PROVENANCE_EXIT": "0",
            "TF_VAR_enable_compute_32_vcpu_candidate": "false",
        }
        for name in (
            "SOCKETLESS_IMAGE",
            "CONTAINER_BUILD_IMAGE",
            "SUPER_LINTER_SEED_IMAGE",
            "COMPUTE_CANDIDATE_IMAGE",
        ):
            self.env.pop(name, None)
        self.contract = self.root / "terraform/runner-pools.json"
        self.set_compute_enabled(True)

    def set_compute_enabled(self, enabled):
        contract = json.loads(self.contract.read_text())
        for pool in contract["pools"].values():
            if pool["profile"] == "compute":
                pool["enabled"] = enabled
        self.contract.write_text(json.dumps(contract))

    def deploy(self, repository="terraform-provider-xcsh", mode="toolchain-canary"):
        return subprocess.run(
            [
                "bash",
                "scripts/arc-deploy.sh",
                "arc/repositories/" + repository + ".yaml",
                mode,
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def commands(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def assert_no_mutations(self):
        for entry in self.commands():
            self.assertFalse(
                entry["command"] == "helm" and entry["args"][0] == "upgrade", entry
            )
            self.assertFalse(
                entry["command"] == "kubectl"
                and entry["args"][0] in {"apply", "delete", "create", "patch"},
                entry,
            )

    def test_only_canary_release_is_upgraded_and_reconciled(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.commands()
        upgrades = [
            row
            for row in calls
            if row["command"] == "helm" and row["args"][0] == "upgrade"
        ]
        self.assertEqual(len(upgrades), 1)
        upgrade = upgrades[0]
        self.assertEqual(upgrade["args"][2], RELEASE)
        self.assertEqual(
            upgrade["args"][upgrade["args"].index("--namespace") + 1], NAMESPACE
        )
        self.assertIn(
            "runnerScaleSetName=terraform-provider-xcsh-toolchain-canary",
            upgrade["args"],
        )
        self.assertIn("minRunners=0", upgrade["args"])
        self.assertIn("maxRunners=1", upgrade["args"])
        self.assertIn(IMAGE, upgrade["values"])
        self.assertIn("runner-profile: compute\n", upgrade["values"])
        self.assertNotIn("runner-profile: compute-toolchain-canary", upgrade["values"])
        self.assertIn("value: compute\n", upgrade["values"])
        self.assertIn("/usr/local/bin/require-landlock-abi", upgrade["values"])
        self.assertNotIn("privileged: true", upgrade["values"])
        self.assertNotIn("DOCKER_HOST", upgrade["values"])
        self.assertIn("ARC listeners ready for 1 runner namespaces", result.stdout)
        attestations = [
            row
            for row in calls
            if row["command"] == "gh" and row["args"][:2] == ["attestation", "verify"]
        ]
        self.assertEqual(len(attestations), 1)
        self.assertIn("oci://" + IMAGE, attestations[0]["args"])
        self.assertIn(SOURCE, attestations[0]["args"])
        self.assertLess(calls.index(attestations[0]), calls.index(upgrade))
        for row in calls:
            if row["command"] == "kubectl" and row["args"][:2] == ["get", "secret"]:
                self.assertEqual(row["args"][row["args"].index("-n") + 1], NAMESPACE)
            self.assertFalse(
                row["command"] == "kubectl"
                and row["args"][0] in {"apply", "delete", "create", "patch"},
                row,
            )

    def test_failed_sbom_refuses_all_upgrades(self):
        self.env["SBOM_EXIT"] = "44"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_no_mutations()
        calls = [row for row in self.commands() if row["command"] == "docker"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["args"][-1], IMAGE)

    def test_failed_provenance_refuses_all_upgrades(self):
        self.env["PROVENANCE_EXIT"] = "43"
        result = self.deploy()
        self.assertEqual(result.returncode, 43, result.stdout + result.stderr)
        self.assert_no_mutations()
        self.assertTrue(
            any(row["args"][:2] == ["attestation", "verify"] for row in self.commands())
        )

    def test_missing_and_mutable_image_or_source_refuse(self):
        for field, value in (
            ("TOOLCHAIN_CANARY_IMAGE", None),
            (
                "TOOLCHAIN_CANARY_IMAGE",
                "ghcr.io/f5-sales-demo/self-hosted-runner:latest",
            ),
            ("TOOLCHAIN_CANARY_SOURCE_SHA", "short"),
        ):
            with self.subTest(field=field, value=value):
                original = self.env[field]
                if value is None:
                    self.env.pop(field)
                else:
                    self.env[field] = value
                result = self.deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_mutations()
                self.env[field] = original

    def test_disabled_compute_pool_refuses(self):
        self.set_compute_enabled(False)
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assert_no_mutations()

    def test_foreign_repository_refuses(self):
        self.assertNotEqual(self.deploy(repository="xcsh").returncode, 0)
        self.assert_no_mutations()


if __name__ == "__main__":
    unittest.main()
