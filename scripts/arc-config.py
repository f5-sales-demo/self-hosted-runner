#!/usr/bin/env python3
"""Validate and normalize one repository-scoped ARC configuration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

PROFILES = {"socketless", "container-build"}
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
    scale_sets = raw["scale_sets"]
    if not isinstance(scale_sets, list) or len(scale_sets) != 2:
        raise ConfigError("scale_sets must contain exactly two entries")

    normalized = []
    unique = {"namespace": set(), "release": set(), "runner_scale_set_name": set()}
    seen_profiles = set()
    root = repository_root.resolve()
    for index, spec in enumerate(scale_sets):
        context = f"scale_sets[{index}]"
        if not isinstance(spec, dict) or set(spec) != SCALE_SET_FIELDS:
            raise ConfigError(
                f"{context} fields must equal {sorted(SCALE_SET_FIELDS)}"
            )
        profile = spec["profile"]
        if profile not in PROFILES or profile in seen_profiles:
            raise ConfigError(f"{context}.profile must uniquely select a supported profile")
        seen_profiles.add(profile)
        for field in unique:
            value = validate_name(spec[field], f"{context}.{field}")
            if value in unique[field]:
                raise ConfigError(f"{field} values must be unique")
            unique[field].add(value)
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
        if relative.is_absolute() or ".." in relative.parts or not values.startswith("arc/"):
            raise ConfigError(f"{context}.values must be a repository-local arc path")
        resolved = (root / relative).resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise ConfigError(f"{context}.values does not name an existing values file")
        expected_values = f"arc/{profile}-values.yaml"
        if values != expected_values:
            raise ConfigError(f"{context}.values must equal {expected_values}")
        normalized.append(dict(spec))
    if seen_profiles != PROFILES:
        raise ConfigError("scale_sets must define socketless and container-build exactly once")
    return {"repository": repository, "scale_sets": normalized}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        config = load_config(args.configuration, root)
    except ConfigError as exc:
        print(f"ARC configuration error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(config, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
