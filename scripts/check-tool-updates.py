#!/usr/bin/env python3
"""Report approved upstream updates without weakening immutable image inputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog/tool-catalog.json"
VERSION = re.compile(r"(?:^|[^0-9])(\d+(?:\.\d+){0,3})(?:$|[^0-9])")


def version_key(value: str) -> tuple[int, ...]:
    match = VERSION.search(value)
    if not match:
        raise ValueError("no numeric version in {}".format(value))
    return tuple(int(part) for part in match.group(1).split("."))


def newer(candidate: str, installed: str) -> bool:
    left = version_key(candidate)
    right = version_key(installed)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def request_json(url: str) -> Any:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "f5-sales-demo-self-hosted-runner-update-check"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def github_release(config: dict[str, Any]) -> str:
    release = request_json("https://api.github.com/repos/{}/releases/latest".format(config["repository"]))
    return str(release["tag_name"])


def npm(config: dict[str, Any]) -> str:
    package = config["package"].replace("/", "%2F")
    document = request_json("https://registry.npmjs.org/{}".format(package))
    return str(document["dist-tags"][config.get("tag", "latest")])


def antigravity_manifest(config: dict[str, Any]) -> str:
    document = request_json(str(config["manifest_url"]))
    return str(document["version"])


def node(config: dict[str, Any], installed: str) -> str:
    major = int(installed.split(".", 1)[0])
    releases = request_json("https://nodejs.org/dist/index.json")
    candidates = [str(item["version"]) for item in releases if int(str(item["version"]).lstrip("v").split(".", 1)[0]) == major]
    if not candidates:
        raise ValueError("no Node {} release".format(major))
    return max(candidates, key=version_key)


def go_module(config: dict[str, Any]) -> str:
    document = request_json("https://proxy.golang.org/{}/@latest".format(config["module"]))
    return str(document["Version"])


def latest(tool: dict[str, Any], config: dict[str, Any]) -> str:
    strategy = config["strategy"]
    if strategy == "github-release":
        return github_release(config)
    if strategy == "npm":
        return npm(config)
    if strategy == "antigravity-manifest":
        return antigravity_manifest(config)
    if strategy == "node":
        return node(config, str(tool["version"]))
    if strategy == "go-module":
        return go_module(config)
    raise ValueError("unsupported update strategy {}".format(strategy))


def check(catalog: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], int]:
    updates: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    update_sources = catalog.get("update_sources", {})
    monitored = 0
    for tool in catalog.get("tools", []):
        name = str(tool.get("name", "unknown"))
        config = update_sources.get(name)
        if not isinstance(config, dict):
            skipped.append({"name": name, "reason": "no update source declared"})
            continue
        monitored += 1
        try:
            candidate = latest(tool, config)
            if newer(candidate, str(tool["version"])):
                updates.append({"name": name, "installed": str(tool["version"]), "available": candidate, "source": str(tool["source"])})
        except (KeyError, TypeError, ValueError, URLError, OSError, json.JSONDecodeError) as error:
            errors.append({"name": name, "error": str(error)})
    return updates, errors, skipped, monitored


def main() -> int:
    parser = argparse.ArgumentParser(description="Check catalogued runner tools for upstream versions")
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--fail-on-update", action="store_true")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    updates, errors, skipped, monitored = check(catalog)
    report = {"updates": updates, "errors": errors, "skipped": skipped, "summary": {"monitored": monitored, "updates": len(updates), "errors": len(errors), "unmonitored": len(skipped)}}
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for update in updates:
            print("UPDATE {name}: {installed} -> {available} ({source})".format(**update))
        for error in errors:
            print("ERROR {name}: {error}".format(**error), file=sys.stderr)
        print("checked {} monitored tools; {} updates; {} unmonitored".format(monitored, len(updates), len(skipped)))
    if errors:
        return 2
    return 1 if updates and args.fail_on_update else 0


if __name__ == "__main__":
    raise SystemExit(main())
