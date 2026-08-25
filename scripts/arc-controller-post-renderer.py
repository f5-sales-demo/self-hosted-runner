#!/usr/bin/env python3
from __future__ import annotations

import sys

SOURCE = "ghcr.io/actions/gha-runner-scale-set-controller:0.14.2"
PINNED = (
    "ghcr.io/actions/gha-runner-scale-set-controller"
    "@sha256:1b4c7f62e971ab259a4b8798e48e2adaad4af747f45990f474ea5feefa03531d"
)

manifest = sys.stdin.read()
count = manifest.count(SOURCE)
if count < 1:
    raise SystemExit("controller image was not present in rendered manifests")
sys.stdout.write(manifest.replace(SOURCE, PINNED))
