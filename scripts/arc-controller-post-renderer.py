#!/usr/bin/env python3
from __future__ import annotations

import re
import sys

# Current stable release; the digest identifies qualified bytes without an age hold.
SOURCE = "ghcr.io/actions/gha-runner-scale-set-controller:0.15.1"
PINNED = (
    "ghcr.io/actions/gha-runner-scale-set-controller"
    "@sha256:4b51a20db717b194e2f3fa96cfe2ddcb21602811f79053c75fd1a383517b9f90"
)

manifest = sys.stdin.read()
pattern = re.compile(
    r'^(?P<prefix>[ \t]*(?P<field>image|value):[ \t]*")'
    + re.escape(SOURCE)
    + r'(?P<suffix>"[ \t]*)$',
    re.MULTILINE,
)
matches = list(pattern.finditer(manifest))
if manifest.count(SOURCE) != 2 or sorted(m["field"] for m in matches) != ["image", "value"]:
    raise SystemExit("expected exactly one current controller image and one controller image environment value")
sys.stdout.write(pattern.sub(lambda m: m["prefix"] + PINNED + m["suffix"], manifest))
