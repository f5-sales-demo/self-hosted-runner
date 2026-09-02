#!/usr/bin/env python3
"""Validate the exact fleet inventory and generate Renovate global config."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_COUNT = 39
REPOSITORY = re.compile(r"^f5-sales-demo/[a-z0-9][a-z0-9-]*$")
OUTPUT = ROOT / "renovate-system/generated/renovate.json"
DOCS_CONTROL = "f5-sales-demo/docs-control"
MANAGED_DOWNSTREAM_WORKFLOWS = [
    ".github/workflows/antigravity-review.yml",
    ".github/workflows/auto-merge.yml",
    ".github/workflows/github-pages-deploy.yml",
    ".github/workflows/require-linked-issue.yml",
    ".github/workflows/semgrep.yml",
    ".github/workflows/super-linter.yml",
    ".github/workflows/translation-audit.yml",
    ".github/workflows/workflow-security-audit.yml",
]


class InventoryError(ValueError):
    pass


def strict_json(path: Path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InventoryError(f"duplicate key in {path}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read {path}: {exc}") from exc


def validate_list(value, source: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise InventoryError(f"{source} repositories must be a string array")
    if len(value) != len(set(value)):
        raise InventoryError(f"{source} repositories contain duplicates")
    if value != sorted(value):
        raise InventoryError(f"{source} repositories must be sorted")
    invalid = [item for item in value if not REPOSITORY.fullmatch(item)]
    if invalid:
        raise InventoryError(f"{source} contains invalid or foreign repositories: {invalid}")
    if "f5-sales-demo/self-hosted-runner" in value:
        raise InventoryError(f"{source} must exclude self-hosted-runner")
    return value


def inventory(root: Path = ROOT) -> list[str]:
    catalog = strict_json(root / "catalog/governed-repositories.json")
    catalog_repos = validate_list(catalog.get("repositories"), "catalog")
    if len(catalog_repos) != EXPECTED_COUNT:
        raise InventoryError(f"catalog must contain exactly {EXPECTED_COUNT} repositories")

    arc_repos = []
    arc_dir = root / "arc/repositories"
    files = sorted(arc_dir.glob("*.yaml"))
    if len(files) != EXPECTED_COUNT:
        raise InventoryError(f"ARC must contain exactly {EXPECTED_COUNT} repository files")
    for path in files:
        document = strict_json(path)
        url = document.get("repository") if isinstance(document, dict) else None
        prefix = "https://github.com/"
        if not isinstance(url, str) or not url.startswith(prefix):
            raise InventoryError(f"{path} has an invalid repository URL")
        repository = url.removeprefix(prefix)
        if path.stem != repository.rsplit("/", 1)[-1]:
            raise InventoryError(f"{path} filename does not match {repository}")
        arc_repos.append(repository)
    arc_repos = validate_list(sorted(arc_repos), "ARC")

    capacity = strict_json(root / "config/arc-capacity.json")
    capacity_repos = validate_list(capacity.get("repositories"), "capacity")
    sources = {"ARC": arc_repos, "capacity": capacity_repos}
    catalog_set = set(catalog_repos)
    for name, values in sources.items():
        missing = sorted(catalog_set - set(values))
        extra = sorted(set(values) - catalog_set)
        if missing or extra:
            raise InventoryError(
                f"{name} inventory differs from catalog: missing={missing} extra={extra}"
            )
    return catalog_repos


def global_config(repositories: list[str]) -> dict:
    command = "node scripts/prepare-generated-artifact-release.mjs prepare"
    return {
        "platform": "github",
        "repositories": repositories,
        "requireConfig": "ignored",
        "onboarding": False,
        "dependencyDashboard": False,
        "labels": [],
        "prConcurrentLimit": 10,
        "timezone": "America/Toronto",
        "minimumReleaseAge": "7 days",
        "internalChecksFilter": "strict",
        "force": {
            "prCreation": "immediate",
            "platformAutomerge": False,
        },
        "allowScripts": False,
        "allowPlugins": False,
        "ignoreScripts": True,
        "allowedCommands": [
            r"^node scripts/prepare-generated-artifact-release\.mjs prepare$"
        ],
        "packageRules": [
            {
                "description": "Bump npm dependency range lower bounds",
                "matchManagers": ["npm"],
                "rangeStrategy": "bump",
            },
            {
                "description": "Pin npm overrides for artifact command compatibility",
                "matchManagers": ["npm"],
                "matchDepTypes": ["overrides"],
                "rangeStrategy": "pin",
            },
            {
                "description": "Preserve the vscode-xcsh Babel 7-compatible Rolldown plugin",
                "matchRepositories": ["f5-sales-demo/vscode-xcsh"],
                "matchManagers": ["npm"],
                "matchPackageNames": ["@rolldown/plugin-babel"],
                "enabled": False,
            },
            {
                "groupName": "npm-minor-patch",
                "matchManagers": ["npm"],
                "matchUpdateTypes": ["minor", "patch"],
                "automerge": True,
                "automergeType": "pr",
                "platformAutomerge": False,
            },
            {
                "groupName": "actions-minor-patch",
                "matchManagers": ["github-actions"],
                "matchUpdateTypes": ["minor", "patch"],
                "automerge": True,
                "automergeType": "pr",
                "platformAutomerge": False,
            },
            {
                "description": "Regenerate docs-icons release artifacts on npm branches",
                "matchRepositories": ["f5-sales-demo/docs-icons"],
                "matchManagers": ["npm"],
                "recreateWhen": "always",
                "postUpgradeTasks": {
                    "commands": [command],
                    "executionMode": "branch",
                    "fileFilters": [
                        "package-lock.json",
                        "packages/*/package.json",
                        "packages/*/icons.json",
                    ],
                },
            },
            {
                "description": "Keep docs-control managed workflows canonical downstream",
                "matchRepositories": [
                    repository
                    for repository in repositories
                    if repository != DOCS_CONTROL
                ],
                "matchFileNames": MANAGED_DOWNSTREAM_WORKFLOWS,
                "enabled": False,
            },
        ],
    }


def render(root: Path = ROOT) -> str:
    return json.dumps(global_config(inventory(root)), indent=2) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = render()
    if args.check:
        try:
            current = OUTPUT.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current != expected:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; regenerate it", file=sys.stderr)
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InventoryError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
