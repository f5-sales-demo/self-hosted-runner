import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PullSecretCopyTests(unittest.TestCase):
    def test_combined_secret_copy_is_fail_closed_and_redacted(self) -> None:
        script = (ROOT / "scripts/arc-copy-pull-secret.sh").read_text(encoding="utf-8")
        self.assertIn("f5.sales-demo/acr-expires-at", script)
        self.assertIn("datetime.timedelta(hours=24)", script)
        self.assertIn('{"ghcr.io", "f5salesdemoarcca.azurecr.io"}', script)
        self.assertIn("chmod 0600", script)
        self.assertNotIn("set -x", script)
        self.assertNotIn('auths")', script)


if __name__ == "__main__":
    unittest.main()
