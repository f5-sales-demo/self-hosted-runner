"""Refuse unsupported installed ARC migrations before live mutation."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from tests import test_provider_canary_cli as canary

ROOT = Path(__file__).resolve().parents[1]

class ArcReleasePreflightTest(unittest.TestCase):
    def check(self, value):
        return subprocess.run([sys.executable, str(ROOT / "scripts/arc-release-preflight.py"), "arc", "gha-runner-scale-set-controller", "0.15.1"], input=value if isinstance(value, str) else json.dumps(value), text=True, capture_output=True)

    def test_fresh_current_and_verified_patch_release_are_eligible(self):
        for releases in ([], [{"name": "arc", "chart": "gha-runner-scale-set-controller-0.15.1", "status": "deployed"}], [{"name": "arc", "chart": "gha-runner-scale-set-controller-0.15.0", "status": "deployed"}]):
            with self.subTest(releases=releases):
                result = self.check(releases)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_prior_minor_requires_documented_cleanup_and_reinstall(self):
        result = self.check([{"name": "arc", "chart": "gha-runner-scale-set-controller-0.14.2", "status": "deployed"}])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewed ARC cleanup and reinstall", result.stderr)

    def test_ambiguous_foreign_interrupted_and_malformed_state_refuses(self):
        current = {"name": "arc", "chart": "gha-runner-scale-set-controller-0.15.1", "status": "deployed"}
        for value in ("malformed", "[] []", {}, [current, current], [{**current, "name": "foreign"}], [{**current, "chart": "foreign-0.15.1"}], [{**current, "status": "pending-upgrade"}]):
            with self.subTest(value=value):
                self.assertNotEqual(self.check(value).returncode, 0)

class ArcMigrationCliTest(unittest.TestCase):
    def test_existing_old_shared_controller_refuses_canary_before_mutation(self):
        fixture = canary.ProviderCanaryCliTest(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.env["CURRENT_CONTROLLER_CHART"] = "gha-runner-scale-set-controller-0.14.2"
        result = fixture.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewed ARC cleanup and reinstall", result.stderr)
        fixture.assert_no_mutations()
