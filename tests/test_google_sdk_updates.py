from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sdk_update_monitor", ROOT / "scripts/check-tool-updates.py"
)
assert SPEC and SPEC.loader
updates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updates)
URL = "https://dl.google.com/dl/cloudsdk/channels/rapid/components-2.json"
STRATEGY = {"strategy": "google-cloud-sdk"}


class GoogleSdkUpdateTests(unittest.TestCase):
    def test_catalog_monitors_google_cli_from_official_rapid_channel(self):
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        self.assertEqual(catalog["update_sources"]["google-cloud-cli"], STRATEGY)

    def test_official_current_version_is_immediately_eligible(self):
        catalog = {
            "tools": [
                {"name": "google-cloud-cli", "version": "579.0.0", "source": URL}
            ],
            "update_sources": {"google-cloud-cli": STRATEGY},
        }
        with patch.object(
            updates, "request_json", return_value={"version": "594.0.1"}
        ) as request:
            found, errors, skipped, monitored = updates.check(catalog)
        request.assert_called_once_with(URL)
        self.assertEqual((errors, skipped, monitored), ([], [], 1))
        self.assertEqual(found[0]["available"], "594.0.1")

    def test_current_version_does_not_propose_a_downgrade_or_duplicate(self):
        catalog = {
            "tools": [
                {"name": "google-cloud-cli", "version": "594.0.1", "source": URL}
            ],
            "update_sources": {"google-cloud-cli": STRATEGY},
        }
        for version in ("594.0.1", "593.0.0"):
            with (
                self.subTest(version=version),
                patch.object(
                    updates, "request_json", return_value={"version": version}
                ),
            ):
                found, errors, skipped, monitored = updates.check(catalog)
                self.assertEqual((found, errors, skipped, monitored), ([], [], [], 1))

    def test_invalid_manifest_is_an_error_and_never_silent_unmonitored(self):
        catalog = {
            "tools": [
                {"name": "google-cloud-cli", "version": "579.0.0", "source": URL}
            ],
            "update_sources": {"google-cloud-cli": STRATEGY},
        }
        for document in (
            {},
            {"version": None},
            {"version": 594},
            {"version": "594.0.1-beta"},
            {"version": "594.0.1 extra"},
            [],
        ):
            with (
                self.subTest(document=document),
                patch.object(updates, "request_json", return_value=document),
            ):
                found, errors, skipped, monitored = updates.check(catalog)
                self.assertEqual((found, skipped, monitored), ([], [], 1))
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["name"], "google-cloud-cli")

    def test_unsupported_strategy_remains_an_explicit_error(self):
        with self.assertRaises(ValueError):
            updates.latest({"version": "1.0.0"}, {"strategy": "unsupported-sdk"})

    def test_current_sdk_image_uses_reviewed_release(self):
        catalog = json.loads((ROOT / "catalog/tool-catalog.json").read_text())
        tool = next(
            item for item in catalog["tools"] if item["name"] == "google-cloud-cli"
        )
        self.assertEqual(tool["version"], "594.0.1")
        self.assertEqual(
            tool["sha256"],
            "c6fdba4cb1551b0da7c1d744eb0dfaf68b59861238d6a6b5ef6bad1ba596b567",
        )
        self.assertEqual(
            tool["source"],
            "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-linux-x86_64.tar.gz",
        )
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("ARG GCLOUD_VERSION=" + tool["version"], dockerfile)
        self.assertIn("ARG GCLOUD_SHA256=" + tool["sha256"], dockerfile)

        self.assertIn("ARG GCLOUD_URL=" + tool["source"], dockerfile)
        self.assertIn("ARG GCLOUD_SIZE=" + str(tool["size_bytes"]), dockerfile)
