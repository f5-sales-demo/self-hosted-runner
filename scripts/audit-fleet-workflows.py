#!/usr/bin/env python3
# Audit all governed self-hosted workflow jobs against the immutable tool catalogue.

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "catalog/tool-catalog.json"
FLEET_PATH = ROOT / "catalog/governed-repositories.json"
EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
ENV_VERSION = re.compile(r"^\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*}}$")
INSTALLER = re.compile(r"\b(?:sudo|apt(?:-get)?\s+(?:install|update)|apk\s+add|brew\s+install|choco\s+install|npm\s+install\s+-g|pip(?:3)?\s+install(?!\s+(?:-r|--requirement))|go\s+install|cargo\s+install|gem\s+install|composer\s+global|curl[^\n]*\|\s*(?:ba)?sh|wget[^\n]*(?:\|\s*(?:ba)?sh|-O\s*/(?:usr|opt|tmp)))")
ACTION_SHA = re.compile(r"^[0-9a-f]{40}$")
LOCKFILE_INSTALL = re.compile(r"\b(?:npm\s+ci|bun\s+install\s+--frozen-lockfile|pip(?:3)?\s+install\s+(?:-r|--requirement)|poetry\s+install|composer\s+install|pnpm\s+install\s+--frozen-lockfile)\b")


@dataclass(frozen=True)
class Finding:
    level: str
    location: str
    message: str


def command(args: list[str]) -> str:
    result = subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError("command failed: {}: {}".format(" ".join(args), result.stderr.strip()))
    return result.stdout


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_self_hosted(value: object) -> bool:
    if isinstance(value, str):
        return value == "self-hosted"
    return isinstance(value, list) and "self-hosted" in value


def action_name(value: str) -> str:
    return value.rsplit("@", 1)[0].lower()


def resolve_version(value: object, environment: object) -> object:
    if not isinstance(value, str) or not isinstance(environment, dict):
        return value
    match = ENV_VERSION.fullmatch(value)
    return environment.get(match.group(1)) if match else value


def exact(value: object) -> bool:
    return isinstance(value, str) and bool(EXACT_VERSION.fullmatch(value))


def audit_job(document: dict, relative: str, job_name: str, job: object, setup: dict) -> Iterable[Finding]:
    if not isinstance(job, dict) or not is_self_hosted(job.get("runs-on")):
        return ()
    findings: list[Finding] = []
    for index, step in enumerate(job.get("steps", [])):
        location = "{}/{}/steps/{}".format(relative, job_name, index)
        if not isinstance(step, dict):
            findings.append(Finding("error", location, "step must be an object"))
            continue
        uses = step.get("uses")
        if isinstance(uses, str) and not uses.startswith(("./", "docker://")):
            name = action_name(uses)
            reference = uses.rsplit("@", 1)[1] if "@" in uses else ""
            if not ACTION_SHA.fullmatch(reference):
                findings.append(Finding("error", location, "marketplace action must be pinned to a full commit SHA: {}".format(uses)))
            if name in setup:
                field = setup[name]["field"]
                version = step.get("with", {}).get(field) if isinstance(step.get("with"), dict) else None
                version = resolve_version(version, document.get("env", {}))
                if not exact(version):
                    findings.append(Finding("error", location, "{} requires exact catalog version, got {!r}".format(name, version)))
                elif version not in setup[name]["versions"]:
                    findings.append(Finding("error", location, "{} version {} is not catalogued".format(name, version)))
            elif name.startswith("actions/setup-") or "/setup-" in name:
                findings.append(Finding("error", location, "uncatalogued setup action {}".format(name)))
            elif "download" in name and name not in {"actions/download-artifact"}:
                findings.append(Finding("error", location, "uncatalogued download action {}".format(name)))
        run = step.get("run")
        if not isinstance(run, str):
            continue
        executable = "\n".join(line.split("#", 1)[0] for line in run.splitlines())
        if LOCKFILE_INSTALL.search(executable):
            findings.append(Finding("info", location, "lockfile dependency installation is job-local"))
        if INSTALLER.search(executable):
            findings.append(Finding("error", location, "direct or privileged tool installer is prohibited on self-hosted runners"))
    return findings


def audit_workflows(repository: str, workflows: dict[str, str], setup: dict) -> list[Finding]:
    findings: list[Finding] = []
    for relative, content in sorted(workflows.items()):
        try:
            document = yaml.safe_load(content) or {}
        except yaml.YAMLError as error:
            findings.append(Finding("error", relative, "cannot parse workflow: {}".format(error)))
            continue
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
            findings.append(Finding("error", relative, "workflow must have a jobs object"))
            continue
        for name, job in document["jobs"].items():
            findings.extend(audit_job(document, "{}/{}".format(repository, relative), str(name), job, setup))
    if not workflows:
        findings.append(Finding("info", repository, "no workflow files"))
    return findings


def checkout_workflows(root: Path, repository: str) -> dict[str, str]:
    directory = root / repository.rsplit("/", 1)[1] / ".github/workflows"
    if not directory.is_dir():
        return {}
    return {path.relative_to(directory.parent.parent).as_posix(): path.read_text(encoding="utf-8") for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))}


def github_workflows(repository: str, ref: str) -> dict[str, str]:
    tree = json.loads(command(["gh", "api", "repos/{}/git/trees/{}?recursive=1".format(repository, ref)]))
    paths = [item["path"] for item in tree.get("tree", []) if item.get("type") == "blob" and item.get("path", "").startswith(".github/workflows/") and item["path"].endswith((".yml", ".yaml"))]
    result = {}
    for path in paths:
        encoded = json.loads(command(["gh", "api", "repos/{}/contents/{}?ref={}".format(repository, path, ref)]))["content"]
        result[path] = base64.b64decode(encoded).decode("utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit all 39 governed self-hosted workflows")
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--fleet", type=Path, default=FLEET_PATH)
    parser.add_argument("--checkouts-root", type=Path)
    parser.add_argument("--github", action="store_true", help="read workflows with authenticated gh api")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--repository", action="append", default=[])
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    if bool(args.checkouts_root) == bool(args.github):
        parser.error("select exactly one of --checkouts-root or --github")
    catalog = load_json(args.catalog)
    repositories = args.repository or load_json(args.fleet)["repositories"]
    if len(repositories) != 39 and not args.repository:
        raise SystemExit("fleet manifest must contain exactly 39 repositories")
    findings: list[Finding] = []
    for repository in repositories:
        workflows = checkout_workflows(args.checkouts_root, repository) if args.checkouts_root else github_workflows(repository, args.ref)
        findings.extend(audit_workflows(repository, workflows, catalog["setup_actions"]))
    errors = [finding for finding in findings if finding.level == "error"]
    if args.format == "json":
        print(json.dumps({"repositories": len(repositories), "errors": len(errors), "findings": [finding.__dict__ for finding in findings]}, indent=2, sort_keys=True))
    else:
        for finding in findings:
            print("[{}] {}: {}".format(finding.level, finding.location, finding.message))
        print("audited {} repositories; {} errors".format(len(repositories), len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
