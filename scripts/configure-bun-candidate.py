#!/usr/bin/env python3
"""Rewrite only the Bun entry in an image-local immutable tool catalogue."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def configure(path: Path, version: str, digest: str) -> None:
    if not VERSION.fullmatch(version):
        raise ValueError("Bun candidate version must be an exact semantic version")
    if not SHA256.fullmatch(digest):
        raise ValueError("Bun candidate SHA-256 must be 64 lowercase hex characters")
    catalog = json.loads(path.read_text(encoding="utf-8"))
    matches = [tool for tool in catalog["tools"] if tool.get("name") == "bun"]
    if len(matches) != 1:
        raise ValueError("tool catalogue must contain exactly one Bun entry")
    matches[0].update(
        {
            "version": version,
            "source": f"https://github.com/oven-sh/bun/releases/download/bun-v{version}/bun-linux-x64.zip",
            "sha256": digest,
            "expected": version,
        }
    )
    path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("version")
    parser.add_argument("sha256")
    args = parser.parse_args()
    configure(args.catalog, args.version, args.sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
