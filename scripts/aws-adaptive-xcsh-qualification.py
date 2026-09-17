#!/usr/bin/env python3
"""Resumable, identity-bound dispatcher for AWS xcsh file-worker qualification."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = "f5-sales-demo/xcsh"
WORKFLOW = "compute-benchmark.yml"


def load_state(path: Path, source_sha: str, image_digest: str) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text())
        if state["source_sha"] != source_sha or state["image_digest"] != image_digest:
            raise ValueError("campaign identities differ; start a new state file")
        return state
    return {"schema_version": 1, "source_sha": source_sha, "image_digest": image_digest, "production_workers": 0, "probes": [], "status": "screening"}


def next_probe(state: dict[str, Any]) -> int | None:
    """Choose an adaptive safe probe; unsafe/failed candidates stop upward search."""
    probes = state["probes"]
    if not probes:
        return 10
    useful = [p["workers"] for p in probes if p.get("safe") and p.get("improvement", 0) >= .03]
    last = probes[-1]
    if not last.get("safe") or last.get("improvement", 0) < .03:
        lower = max([0, *useful])
        upper = last["workers"]
        return (lower + upper) // 2 if upper - lower > 2 else None
    if last["workers"] == 10:
        return 20
    if last["workers"] == 20:
        return 30
    if last["workers"] == 30:
        return 32
    return None


def record_evidence(state: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Admit only source/image-bound, resource-safe screening evidence."""
    required = {"run_id", "workers", "source_sha", "image_digest", "output_equivalent", "failures", "ooms", "evictions", "restarts", "memory_ratio", "node_pressure", "cpu_throttled", "disk_saturated", "improvement"}
    missing = required - evidence.keys()
    if missing:
        raise ValueError(f"evidence missing required fields: {sorted(missing)}")
    if evidence["source_sha"] != state["source_sha"] or evidence["image_digest"] != state["image_digest"]:
        raise ValueError("evidence identity does not match frozen campaign")
    safe = (
        bool(evidence["output_equivalent"])
        and all(int(evidence[key]) == 0 for key in ("failures", "ooms", "evictions", "restarts"))
        and float(evidence["memory_ratio"]) < .8
        and not any(bool(evidence[key]) for key in ("node_pressure", "cpu_throttled", "disk_saturated"))
    )
    state["probes"].append({"workers": int(evidence["workers"]), "run_id": str(evidence["run_id"]), "safe": safe, "improvement": float(evidence["improvement"]), "memory_ratio": float(evidence["memory_ratio"])})
    if not safe:
        state["status"] = "screening-stopped-unsafe"
    elif float(evidence["memory_ratio"]) >= .75:
        state["status"] = "screening-stopped-resource"


def dispatch(source_sha: str, workers: int, cache_state: str, pair_id: int, dry_run: bool) -> None:
    command = ["gh", "workflow", "run", WORKFLOW, "--repo", REPOSITORY, "--ref", source_sha,
               "-f", f"source_sha={source_sha}", "-f", "experiment=d16-parallel",
               "-f", f"file_workers={workers}", "-f", f"cache_state={cache_state}", "-f", f"pair_id={pair_id}"]
    if not dry_run:
        subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--record", type=Path, help="redacted JSON evidence for one completed run")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if len(args.source_sha) != 40 or any(c not in "0123456789abcdef" for c in args.source_sha):
        parser.error("--source-sha must be a lowercase 40-character SHA")
    if "@sha256:" not in args.image_digest:
        parser.error("--image-digest must be immutable")
    state = load_state(args.state, args.source_sha, args.image_digest)
    if args.record:
        record_evidence(state, json.loads(args.record.read_text()))
    worker = next_probe(state)
    if worker is None:
        state["status"] = "screening-complete"
    elif args.dispatch:
        dispatch(args.source_sha, worker, "warm", len(state["probes"]) + 1, args.dry_run)
        state["dispatched"] = {"workers": worker, "cache_state": "warm"}
    args.state.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
