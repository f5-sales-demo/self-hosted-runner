#!/usr/bin/env python3
"""Check exact installed release state before any ARC deployment mutation."""
from __future__ import annotations

import json
import sys

if len(sys.argv) not in (4, 5) or (len(sys.argv) == 5 and sys.argv[4] != "--require-installed"):
    raise SystemExit("usage: arc-release-preflight.py <release> <chart> <version> [--require-installed]")
release, chart, version = sys.argv[1:4]
try:
    installed = json.load(sys.stdin)
except (ValueError, UnicodeError):
    raise SystemExit("installed ARC release state is malformed") from None
if not isinstance(installed, list) or len(installed) > 1:
    raise SystemExit("installed ARC release state is ambiguous")
if not installed:
    if len(sys.argv) == 5:
        raise SystemExit("current ARC controller installation is required before deploying runners")
    raise SystemExit(0)
item = installed[0]
if not isinstance(item, dict) or item.get("name") != release or item.get("status") != "deployed":
    raise SystemExit("installed ARC release identity is foreign or its operation is incomplete")
qualified = {chart + "-" + version}
# Official 0.15.1 release guidance explicitly permits this patch transition
# without CRD replacement. Other transitions require their own reviewed mapping.
if version == "0.15.1":
    qualified.add(chart + "-0.15.0")
if item.get("chart") not in qualified:
    raise SystemExit(
        "installed chart requires reviewed ARC cleanup and reinstall before this version; "
        "uninstall runner scale sets, verify cleanup, reinstall the controller and handle changed CRDs "
        "according to https://docs.github.com/en/actions/hosting-your-own-runners/"
        "managing-self-hosted-runners-with-actions-runner-controller/"
        "deploying-runner-scale-sets-with-actions-runner-controller#upgrading-arc"
    )
