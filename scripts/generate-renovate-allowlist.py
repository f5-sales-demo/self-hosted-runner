#!/usr/bin/env python3
"""Generate the Renovate repository allowlist from the authoritative catalog."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
catalog = json.loads((ROOT / "catalog/governed-repositories.json").read_text(encoding="utf-8"))
repos = catalog.get("repositories")
if not isinstance(repos, list) or len(repos) != 39 or len(set(repos)) != 39 or any(not isinstance(r, str) or not r.startswith("f5-sales-demo/") for r in repos):
    raise SystemExit("catalog must contain exactly 39 unique f5-sales-demo repositories")
output = ROOT / "renovate-system/generated/repositories.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(sorted(repos), indent=2) + "\n", encoding="utf-8")
config = json.loads((ROOT / "renovate-system/renovate.json").read_text(encoding="utf-8"))
config["repositories"] = sorted(repos)
(output.parent / "renovate.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
