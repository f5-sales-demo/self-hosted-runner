#!/usr/bin/env python3
# Verify the immutable image contract without downloading any runtime tools.

from __future__ import annotations

import argparse
import json
import os
import shlex
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
    for runner_root in (Path("/home/runner"), Path("/opt/actions-runner")):
        for executable in ("config.sh", "run.sh"):
            path = runner_root / executable
            if not path.is_file() or not os.access(path, os.X_OK):
                failures.append(f"runner payload is missing executable {path}")
            else:
                print(f"[ok] runner payload {path}")
    if Path("/opt/actions-runner").is_symlink():
        failures.append("legacy runner payload path must be a real directory")
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
        markers = list(cache.get("cache_paths", {}).values())
        marker_value = cache.get("cache_path")
        if marker_value:
            markers.append(marker_value)
        for marker in markers:
            if not Path(marker).is_file():
                failures.append("{}: missing tool-cache marker {}".format(action, marker))
        print("[ok] {} catalog {}".format(action, ", ".join(cache["versions"])))
    for version, marker in raw["setup_actions"].get("actions/setup-python", {}).get("cache_paths", {}).items():
        tool_bin = Path(marker).with_suffix("") / "bin"
        code, output = run("PATH={}:$PATH; export PATH; command -v python; python --version; command -v pip; pip --version".format(shlex.quote(str(tool_bin))))
        expected = "Python {}".format(version)
        if code or str(tool_bin / "python") not in output or str(tool_bin / "pip") not in output or expected not in output:
            failures.append("actions/setup-python {}: cache PATH did not resolve its python and pip entrypoints: {!r}".format(version, output.strip()))
        else:
            print("[ok] actions/setup-python {} cache entrypoints".format(version))
    docker_code, _ = run("command -v docker")
    if args.profile == "standard" and docker_code == 0:
        failures.append("standard profile unexpectedly contains docker")
    if args.profile == "container-build" and docker_code != 0:
        failures.append("container-build profile is missing docker")
    cross_code, cross_output = run(
        "printf '#include <stdio.h>\\nint main(void) { return 0; }\\n' "
        "| aarch64-linux-gnu-gcc -x c - -o /tmp/runner-arm64-libc-smoke "
        "&& rm -f /tmp/runner-arm64-libc-smoke"
    )
    if cross_code:
        failures.append("ARM64 cross compiler cannot use the target libc sysroot: {!r}".format(cross_output.strip()))
    else:
        print("[ok] ARM64 cross compiler libc sysroot")
    for compiler, language, source in (
        ("clang", "c", "int main(void) { return 0; }"),
        ("clang++", "c++", "int main() { return 0; }"),
    ):
        output_path = "/tmp/runner-{}-smoke".format(compiler)
        compiler_code, compiler_output = run(
            "printf '%s\\n' {} | {} -x {} - -o {} && {} && rm -f {}".format(
                shlex.quote(source), compiler, language, output_path, output_path, output_path
            )
        )
        if compiler_code:
            failures.append("{} cannot compile and run a {} smoke test: {!r}".format(compiler, language, compiler_output.strip()))
        else:
            print("[ok] {} {} smoke test".format(compiler, language))
    if failures:
        print("\n".join("[error] {}".format(failure) for failure in failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
