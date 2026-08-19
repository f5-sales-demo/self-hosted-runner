#!/usr/bin/env python3
# Verify the immutable image contract without downloading any runtime tools.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

CATALOG = Path("/usr/local/share/runner-catalog/tool-catalog.json")


def run(command: str) -> tuple[int, str]:
    result = subprocess.run(command, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return result.returncode, result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("standard", "container-build"))
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    args = parser.parse_args()
    raw = json.loads(args.catalog.read_text(encoding="utf-8"))
    failures: list[str] = []
    for tool in raw["tools"]:
        if args.profile not in tool["profiles"]:
            continue
        code, output = run(tool["command"])
        expected = tool.get("expected", "")
        if code or (expected and expected not in output):
            failures.append("{}: command={!r} code={} output={!r}".format(tool["name"], tool["command"], code, output.strip()))
        else:
            print("[ok] {} {}".format(tool["name"], tool.get("version", "")))
    for action, cache in raw["setup_actions"].items():
        marker_value = cache.get("cache_path")
        if marker_value and not Path(marker_value).is_file():
            failures.append("{}: missing tool-cache marker {}".format(action, marker_value))
        else:
            print("[ok] {} catalog {}".format(action, cache["versions"][0]))
    docker_code, _ = run("command -v docker")
    if args.profile == "standard" and docker_code == 0:
        failures.append("standard profile unexpectedly contains docker")
    if args.profile == "container-build" and docker_code != 0:
        failures.append("container-build profile is missing docker")
    if failures:
        print("\n".join("[error] {}".format(failure) for failure in failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
