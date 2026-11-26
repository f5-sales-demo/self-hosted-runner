#!/usr/bin/env python3
"""Verify the SPDX payload selected from an immutable image's Linux/amd64 SBOM."""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse


def validate(payload: object) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("SPDX"), dict):
        raise ValueError("missing SPDX document")
    document = payload["SPDX"]
    if document.get("spdxVersion") not in ("SPDX-2.2", "SPDX-2.3"):
        raise ValueError("unsupported SPDX version")
    if document.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise ValueError("invalid SPDX document identity")
    if document.get("dataLicense") != "CC0-1.0":
        raise ValueError("invalid SPDX data license")
    if not isinstance(document.get("name"), str) or not document["name"].strip():
        raise ValueError("missing SPDX document name")
    namespace = document.get("documentNamespace")
    if not isinstance(namespace, str) or not urlparse(namespace).scheme:
        raise ValueError("invalid SPDX document namespace")
    creation = document.get("creationInfo")
    if not isinstance(creation, dict):
        raise ValueError("missing SPDX creation information")
    if not isinstance(creation.get("created"), str) or not creation["created"]:
        raise ValueError("missing SPDX creation time")
    creators = creation.get("creators")
    if not isinstance(creators, list) or not creators or not all(
        isinstance(creator, str) and creator.strip() for creator in creators
    ):
        raise ValueError("missing SPDX creator")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("empty SPDX package inventory")
    identities: set[str] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("invalid SPDX package")
        name = package.get("name")
        identity = package.get("SPDXID")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("missing SPDX package name")
        if not isinstance(identity, str) or not re.fullmatch(r"SPDXRef-[A-Za-z0-9.-]+", identity):
            raise ValueError("invalid SPDX package identity")
        if identity in identities:
            raise ValueError("duplicate SPDX package identity")
        identities.add(identity)
    return len(packages)


def main() -> int:
    try:
        count = validate(json.load(sys.stdin))
    except (ValueError, TypeError, OSError):
        print("registry SPDX SBOM is missing, malformed, or incomplete", file=sys.stderr)
        return 1
    print(f"verified SPDX SBOM packages={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
