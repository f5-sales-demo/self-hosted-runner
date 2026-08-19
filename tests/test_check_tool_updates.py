import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_tool_updates", ROOT / "scripts/check-tool-updates.py")
assert SPEC and SPEC.loader
updates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updates)


class CheckToolUpdatesTest(unittest.TestCase):
    def test_version_comparison_accepts_release_prefix(self):
        self.assertTrue(updates.newer("v2.3.4", "2.3.3"))
        self.assertFalse(updates.newer("v2.3.4", "2.3.4"))

    def test_report_includes_update_and_unmonitored_tool(self):
        original = updates.latest
        try:
            updates.latest = lambda tool, config: "2.0.0"
            report, errors, skipped, monitored = updates.check({"update_sources": {"runner": {"strategy": "node"}}, "tools": [{"name": "runner", "version": "1.0.0", "source": "test"}, {"name": "snapshot", "version": "1.0.0", "source": "test"}]})
            self.assertEqual([], errors)
            self.assertEqual("runner", report[0]["name"])
            self.assertEqual(1, monitored)
            self.assertEqual("snapshot", skipped[0]["name"])
        finally:
            updates.latest = original
