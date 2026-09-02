#!/usr/bin/env python3
"""Validate and normalize repository-scoped ARC configurations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

PROFILES = {"socketless", "container-build", "compute"}
REQUIRED_PROFILES = {"socketless", "container-build"}
TOP_FIELDS = {"repository", "scale_sets"}
SCALE_SET_FIELDS = {
    "profile",
    "namespace",
    "release",
    "runner_scale_set_name",
    "values",
    "min_runners",
    "max_runners",
}
REPOSITORY_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
DNS_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
DOCS_COHORT = {
    f"https://github.com/f5-sales-demo/{name}"
    for name in (
        "docs",
        "docs-builder",
        "docs-icons",
        "docs-theme",
        "i18n-core",
        "starlight-llms-txt",
    )
}
DOCS_SHARED_LABELS = {
    "socketless": "docs-socketless",
    "container-build": "docs-container-build",
}
MANAGED_COHORT = {
    f"https://github.com/f5-sales-demo/{name}"
    for name in (
        "administration",
        "api-protection",
        "api-specs",
        "api-specs-enriched",
        "apt-repo",
        "bot-advanced",
        "bot-standard",
        "cdn",
        "cdn-simulator",
        "console",
        "csd",
        "ddos",
        "demo-resource-template",
        "demo-resources",
        "devcontainer",
        "dns",
        "docs-control",
        "marketplace",
        "marketplace-claude-code",
        "mcn",
        "nginx",
        "observability",
        "origin-server",
        "starlight-mega-menu",
        "terraform-provider-xcsh",
        "traffic-generator",
        "vscode-xcsh",
        "waf",
        "was",
        "webapp-api-protection",
        "xcsh-action",
        "xcsh-chrome-extension",
    )
}
MANAGED_SHARED_LABELS = {
    "socketless": "managed-socketless",
    "container-build": "managed-container-build",
}
EXPECTED_CAPS = {
    "https://github.com/f5-sales-demo/xcsh": (10, 3, 2),
    "https://github.com/f5-sales-demo/docs": (3, 1),
    "https://github.com/f5-sales-demo/docs-builder": (4, 2),
    "https://github.com/f5-sales-demo/docs-icons": (3, 1),
    "https://github.com/f5-sales-demo/docs-theme": (3, 1),
    "https://github.com/f5-sales-demo/i18n-core": (3, 1),
    "https://github.com/f5-sales-demo/starlight-llms-txt": (3, 1),
    **{repository: (3, 1) for repository in MANAGED_COHORT},
    **{
        f"https://github.com/f5-sales-demo/{name}": limits
        for name, limits in {
            "docs-control": (8, 2),
            "api-specs": (6, 2),
            "api-specs-enriched": (6, 2, 2),
            "terraform-provider-xcsh": (6, 2, 3),
            "devcontainer": (4, 2),
            "console": (4, 1),
            "marketplace": (4, 1),
            "marketplace-claude-code": (4, 1),
            "mcn": (4, 1),
            "origin-server": (4, 1),
            "starlight-mega-menu": (4, 1),
            "vscode-xcsh": (4, 1),
            "xcsh-action": (4, 1),
            "xcsh-chrome-extension": (4, 1),
        }.items()
    },
}


class ConfigError(ValueError):
    """A deterministic repository ARC configuration error."""


def strict_json(path: Path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ConfigError(f"duplicate key in {path}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read ARC configuration {path}: {exc}") from exc


def validate_name(value, context):
    if not isinstance(value, str) or not DNS_NAME_RE.fullmatch(value):
        raise ConfigError(f"{context} must be a DNS-safe Kubernetes name")
    return value


def load_config(path: Path, repository_root: Path):
    raw = strict_json(path)
    if not isinstance(raw, dict) or set(raw) != TOP_FIELDS:
        raise ConfigError(f"configuration fields must equal {sorted(TOP_FIELDS)}")
    repository = raw["repository"]
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise ConfigError("repository must be an exact HTTPS GitHub repository URL")
    if repository not in EXPECTED_CAPS:
        raise ConfigError(f"repository is outside the exact ARC fleet: {repository}")
    scale_sets = raw["scale_sets"]
    if not isinstance(scale_sets, list) or len(scale_sets) not in (2, 3):
        raise ConfigError(
            "scale_sets must contain two required entries and at most one optional entry"
        )

    normalized = []
    unique = {"namespace": set(), "release": set(), "runner_scale_set_name": set()}
    seen_profiles = set()
    root = repository_root.resolve()
    for index, spec in enumerate(scale_sets):
        context = f"scale_sets[{index}]"
        if not isinstance(spec, dict) or set(spec) != SCALE_SET_FIELDS:
            raise ConfigError(f"{context} fields must equal {sorted(SCALE_SET_FIELDS)}")
        profile = spec["profile"]
        if profile not in PROFILES or profile in seen_profiles:
            raise ConfigError(
                f"{context}.profile must uniquely select a supported profile"
            )
        seen_profiles.add(profile)
        compute_allowlist = {
            "https://github.com/f5-sales-demo/xcsh",
            "https://github.com/f5-sales-demo/api-specs-enriched",
            "https://github.com/f5-sales-demo/terraform-provider-xcsh",
        }
        if profile == "compute" and repository not in compute_allowlist:
            raise ConfigError("compute profile is outside the exact approved allowlist")
        for field, seen_values in unique.items():
            value = validate_name(spec[field], f"{context}.{field}")
            if value in seen_values:
                raise ConfigError(f"{field} values must be unique")
            seen_values.add(value)
        minimum = spec["min_runners"]
        maximum = spec["max_runners"]
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
            raise ConfigError(f"{context}.min_runners must be a nonnegative integer")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
            raise ConfigError(f"{context}.max_runners must be a positive integer")
        if minimum > maximum:
            raise ConfigError(f"{context}.min_runners cannot exceed max_runners")
        values = spec["values"]
        if not isinstance(values, str):
            raise ConfigError(f"{context}.values must be a repository-local path")
        relative = PurePosixPath(values)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not values.startswith("arc/")
        ):
            raise ConfigError(f"{context}.values must be a repository-local arc path")
        resolved = (root / relative).resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise ConfigError(f"{context}.values does not name an existing values file")
        expected_values = f"arc/{profile}-values.yaml"
        if values != expected_values:
            raise ConfigError(f"{context}.values must equal {expected_values}")
        normalized.append(dict(spec))
    if not REQUIRED_PROFILES.issubset(seen_profiles):
        raise ConfigError(
            "scale_sets must define socketless and container-build exactly once"
        )
    config = {"repository": repository, "scale_sets": normalized}
    for spec in normalized:
        label = spec["runner_scale_set_name"]
        minimum = spec["min_runners"]
        maximum = spec["max_runners"]
        contracts = (
            (DOCS_COHORT, DOCS_SHARED_LABELS, "documentation"),
            (MANAGED_COHORT, MANAGED_SHARED_LABELS, "managed"),
        )
        for cohort, shared_labels, name in contracts:
            if spec["profile"] == "compute":
                continue
            expected = shared_labels.get(spec["profile"])
            if repository in cohort and label != expected:
                raise ConfigError(
                    f"{repository} {spec['profile']} runner scale set name must equal {expected}"
                )
            if repository not in cohort and label in shared_labels.values():
                raise ConfigError(
                    f"{label} is reserved for the repository-scoped {name} cohort"
                )
        if repository in MANAGED_COHORT:
            name = repository.rsplit("/", 1)[1]
            expected_namespace = f"arc-runners-{name}-{spec['profile']}"
            expected_release = f"{name}-{spec['profile']}"
            if spec["namespace"] != expected_namespace:
                raise ConfigError(
                    f"{repository} {spec['profile']} namespace must equal {expected_namespace}"
                )
            if spec["release"] != expected_release:
                raise ConfigError(
                    f"{repository} {spec['profile']} release must equal {expected_release}"
                )
            if minimum != 0:
                raise ConfigError(f"{repository} min_runners must equal zero")
        cap_index = {"socketless": 0, "container-build": 1, "compute": 2}[
            spec["profile"]
        ]
        caps = EXPECTED_CAPS[repository]
        if cap_index >= len(caps):
            raise ConfigError(
                f"{repository} is not approved for the {spec['profile']} profile"
            )
        expected_maximum = caps[cap_index]
        if maximum != expected_maximum:
            raise ConfigError(
                f"{repository} {spec['profile']} max_runners must equal {expected_maximum}"
            )
    return config


def validate_config_set(paths: list[Path], repository_root: Path):
    """Load configs and reject cross-repository identity collisions."""
    configs = [load_config(path, repository_root) for path in paths]
    repositories = set()
    identities = {
        "namespace": {},
        "release": {},
        "runner_scale_set_name": {},
    }
    for config in configs:
        repository = config["repository"]
        if repository in repositories:
            raise ConfigError(f"repository is configured more than once: {repository}")
        repositories.add(repository)
        for spec in config["scale_sets"]:
            for field, observed in identities.items():
                value = spec[field]
                previous = observed.get(value)
                if previous is None:
                    observed[value] = repository
                    continue
                shared_label_collision = field == "runner_scale_set_name" and any(
                    value == labels.get(spec["profile"])
                    and previous in cohort
                    and repository in cohort
                    for cohort, labels in (
                        (DOCS_COHORT, DOCS_SHARED_LABELS),
                        (MANAGED_COHORT, MANAGED_SHARED_LABELS),
                    )
                )
                if shared_label_collision:
                    continue
                raise ConfigError(
                    f"{field} value {value} collides between {previous} and {repository}"
                )
    return configs


def validate_complete_config_set(paths: list[Path], repository_root: Path):
    """Require the checked-in configuration set to cover the exact ARC fleet."""
    configs = validate_config_set(paths, repository_root)
    observed = {config["repository"] for config in configs}
    catalog = json.loads((repository_root / "catalog/governed-repositories.json").read_text(encoding="utf-8"))
    expected = {f"https://github.com/{repository}" for repository in catalog["repositories"]}
    if len(expected) != 39 or any(not repository.startswith("https://github.com/f5-sales-demo/") for repository in expected):
        raise ConfigError("governed repository catalog must contain exactly 39 unique f5-sales-demo repositories")
    if observed != expected:
        raise ConfigError(
            "ARC configuration coverage mismatch: "
            f"missing={sorted(expected - observed)}, extra={sorted(observed - expected)}"
        )
    return configs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path, nargs="+")
    parser.add_argument(
        "--validate-set",
        action="store_true",
        help="validate cross-configuration identities and emit a normalized array",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        if args.validate_set:
            result = validate_complete_config_set(args.configuration, root)
        elif len(args.configuration) == 1:
            result = load_config(args.configuration[0], root)
        else:
            parser.error("multiple configurations require --validate-set")
    except ConfigError as exc:
        print(f"ARC configuration error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
