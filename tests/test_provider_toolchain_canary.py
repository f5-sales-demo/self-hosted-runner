"""Provider-only canary keeps existing pool and stable deployment identities."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "arc_config_canary", ROOT / "scripts/arc-config.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
PROFILE = "compute-toolchain-canary"
REPOSITORY = "https://github.com/f5-sales-demo/terraform-provider-xcsh"


class ProviderToolchainCanaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "arc").mkdir()
        self.config = json.loads(
            (ROOT / "arc/repositories/terraform-provider-xcsh.yaml").read_text()
        )
        self.config["scale_sets"] = [
            row for row in self.config["scale_sets"] if row["profile"] != PROFILE
        ]
        self.stable = copy.deepcopy(self.config["scale_sets"])
        for row in self.config["scale_sets"]:
            path = self.root / row["values"]
            path.write_bytes((ROOT / row["values"]).read_bytes())
        canary = copy.deepcopy(
            next(row for row in self.stable if row["profile"] == "compute")
        )
        canary.update(
            {
                "profile": PROFILE,
                "namespace": "arc-runners-terraform-provider-xcsh-" + PROFILE,
                "release": "terraform-provider-xcsh-" + PROFILE,
                "runner_scale_set_name": "terraform-provider-xcsh-toolchain-canary",
                "values": "arc/" + PROFILE + "-values.yaml",
                "min_runners": 0,
                "max_runners": 1,
            }
        )
        (self.root / canary["values"]).write_bytes(
            (ROOT / "arc/compute-values.yaml").read_bytes()
        )
        self.config["scale_sets"].append(canary)
        self.path = self.root / "config.json"

    def load(self):
        self.path.write_text(json.dumps(self.config))
        return module.load_config(self.path, self.root)

    def test_provider_canary_admits_without_changing_stable_releases(self):
        result = self.load()
        self.assertEqual(result["scale_sets"][:-1], self.stable)
        self.assertEqual(result["scale_sets"][-1]["max_runners"], 1)
        self.assertEqual(result["scale_sets"][-1]["min_runners"], 0)

    def test_canary_capacity_changes_refuse(self):
        for field, value in (("min_runners", 1), ("max_runners", 2)):
            with self.subTest(field=field):
                original = self.config["scale_sets"][-1][field]
                self.config["scale_sets"][-1][field] = value
                with self.assertRaises(module.ConfigError):
                    self.load()
                self.config["scale_sets"][-1][field] = original

    def test_canary_requires_explicit_opt_in_and_enabled_compute_pool(self):
        (self.root / "terraform").mkdir()
        contract = json.loads((ROOT / "terraform/runner-pools.json").read_text())
        for pool in contract["pools"].values():
            if pool["profile"] == "compute":
                pool["enabled"] = True
        path = self.root / "terraform/runner-pools.json"
        path.write_text(json.dumps(contract))
        config = self.load()
        default = module.enabled_config(config, self.root)
        self.assertNotIn(PROFILE, {row["profile"] for row in default["scale_sets"]})
        enabled = module.enabled_config(config, self.root, enable_toolchain_canary=True)
        self.assertIn(PROFILE, {row["profile"] for row in enabled["scale_sets"]})
        for pool in contract["pools"].values():
            if pool["profile"] == "compute":
                pool["enabled"] = False
        path.write_text(json.dumps(contract))
        disabled = module.enabled_config(
            config, self.root, enable_toolchain_canary=True
        )
        self.assertNotIn(PROFILE, {row["profile"] for row in disabled["scale_sets"]})

    def test_foreign_label_and_duplicate_identity_refuse(self):
        row = self.config["scale_sets"][-1]
        original = row["runner_scale_set_name"]
        row["runner_scale_set_name"] = "foreign-canary"
        with self.assertRaises(module.ConfigError):
            self.load()
        row["runner_scale_set_name"] = original
        row["namespace"] = self.stable[0]["namespace"]
        with self.assertRaises(module.ConfigError):
            self.load()

    def test_foreign_repository_cannot_claim_provider_canary(self):
        # Admit the attempted canary first so unrelated stable checks cannot mask it.
        self.config["scale_sets"] = [
            self.config["scale_sets"][-1],
            *self.config["scale_sets"][:-1],
        ]
        for name in ("xcsh", "api-specs-enriched", "marketplace"):
            with self.subTest(repository=name):
                self.config["repository"] = "https://github.com/f5-sales-demo/" + name
                with self.assertRaisesRegex(
                    module.ConfigError, "reserved for the exact provider label"
                ):
                    self.load()

    def test_logical_canary_profile_reuses_existing_compute_pool(self):
        self.assertEqual(module.TOOLCHAIN_CANARY_POOL_PROFILE, "compute")
        self.assertIn(PROFILE, module.COMPUTE_PROFILES)


if __name__ == "__main__":
    unittest.main()
