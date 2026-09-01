import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PullSecretCopyTests(unittest.TestCase):
    def test_ghcr_only_secret_copy_is_fail_closed_and_redacted(self) -> None:
        script = (ROOT / "scripts/arc-copy-pull-secret.sh").read_text(encoding="utf-8")
        self.assertIn('set(config.get("auths", {})) != {"ghcr.io"}', script)
        self.assertIn("must contain exactly the approved GHCR credential", script)
        self.assertNotIn("f5salesdemoarcca.azurecr.io", script)
        self.assertNotIn("f5.sales-demo/acr-expires-at", script)
        self.assertIn("chmod 0600", script)
        self.assertIn('kubectl apply -f "$manifest_dir"', script)
        self.assertNotIn('kubectl apply -f "$tmpdir"', script)
        self.assertNotIn("set -x", script)
        self.assertNotIn('auths")', script)


if __name__ == "__main__":
    unittest.main()
