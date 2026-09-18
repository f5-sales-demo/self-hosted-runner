#!/usr/bin/env python3
"""Dispatch and evaluate one identity-bound AWS xcsh parallel=20 pair."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = "f5-sales-demo/xcsh"
WORKFLOW = "compute-benchmark.yml"
WORKFLOW_REF = "main"
CONTROL_WORKERS = 0
CANDIDATE_WORKERS = 20
CACHE_STATE = "cold"


def load_state(path: Path, source_sha: str, image_digest: str) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text())
        if state["source_sha"] != source_sha or state["image_digest"] != image_digest:
            raise ValueError("campaign identities differ; start a new state file")
        return state
    return {
        "schema_version": 3,
        "source_sha": source_sha,
        "image_digest": image_digest,
        "production_workers": 10,
        "rollback_workers": 0,
        "candidate_workers": CANDIDATE_WORKERS,
        "evidence": [],
        "status": "qualification",
    }


def safe_evidence(evidence: dict[str, Any]) -> bool:
    return (
        bool(evidence["output_equivalent"])
        and bool(evidence["manifest_equivalent"])
        and bool(evidence["inventory_complete"])
        and all(
            int(evidence[key]) == 0
            for key in ("failures", "ooms", "evictions", "restarts")
        )
        and float(evidence["memory_ratio"]) < 0.8
        and not bool(evidence["node_pressure"])
    )


def record_evidence(state: dict[str, Any], evidence: dict[str, Any]) -> None:
    required = {
        "run_id",
        "role",
        "workers",
        "source_sha",
        "image_digest",
        "output_equivalent",
        "manifest_equivalent",
        "inventory_complete",
        "failures",
        "ooms",
        "evictions",
        "restarts",
        "memory_ratio",
        "node_pressure",
        "typescript_seconds",
        "critical_path_seconds",
        "pod_cpu",
        "pod_memory",
        "aws_quota",
    }
    missing = required - evidence.keys()
    if missing:
        raise ValueError(f"evidence missing required fields: {sorted(missing)}")
    if (
        evidence["source_sha"] != state["source_sha"]
        or evidence["image_digest"] != state["image_digest"]
    ):
        raise ValueError("evidence identity does not match frozen campaign")
    expected_workers = {
        "qualification-serial": CONTROL_WORKERS,
        "qualification-candidate": CANDIDATE_WORKERS,
    }
    role = evidence["role"]
    if (
        role not in expected_workers
        or int(evidence["workers"]) != expected_workers[role]
    ):
        raise ValueError("evidence role or worker count is outside the fixed pair")
    if role == "qualification-candidate":
        if (str(evidence["pod_cpu"]), str(evidence["pod_memory"])) != ("30", "56Gi"):
            raise ValueError("candidate evidence must prove 30 CPU and 56Gi")
        if int(evidence["aws_quota"]) < 660:
            raise ValueError(
                "candidate evidence must prove the approved 660-vCPU quota"
            )
    record = dict(evidence)
    record["run_id"] = str(record["run_id"])
    record["safe"] = safe_evidence(record)
    state.setdefault("evidence", []).append(record)


def evaluate_qualification(state: dict[str, Any]) -> dict[str, Any]:
    evidence = state.get("evidence", [])
    serial = [item for item in evidence if item.get("role") == "qualification-serial"]
    candidate = [
        item for item in evidence if item.get("role") == "qualification-candidate"
    ]
    report = {
        "production_workers": 10,
        "candidate_workers": 20,
        "rollback_workers": 0,
        "promotable": False,
    }
    if len(serial) != 1 or len(candidate) != 1:
        report["reason"] = "incomplete-pair"
        return report
    if not serial[0]["safe"] or not candidate[0]["safe"]:
        report["reason"] = "unsafe-evidence"
        return report
    for metric in ("typescript_seconds", "critical_path_seconds"):
        if float(candidate[0][metric]) >= float(serial[0][metric]):
            report["reason"] = f"{metric}-did-not-decrease"
            return report
    report["promotable"] = True
    report["run_ids"] = [serial[0]["run_id"], candidate[0]["run_id"]]
    return report


def next_dispatch(state: dict[str, Any]) -> dict[str, Any] | None:
    completed = {item.get("role") for item in state.setdefault("evidence", [])}
    if "qualification-serial" not in completed:
        return {
            "role": "qualification-serial",
            "experiment": "d16-serial",
            "workers": 0,
            "cache_state": CACHE_STATE,
            "pair_id": 1,
        }
    if "qualification-candidate" not in completed:
        return {
            "role": "qualification-candidate",
            "experiment": "f32-parallel",
            "workers": 20,
            "cache_state": CACHE_STATE,
            "pair_id": 1,
        }
    state["qualification_report"] = evaluate_qualification(state)
    state["status"] = (
        "promotion-ready"
        if state["qualification_report"]["promotable"]
        else "qualification-rejected"
    )
    return None


def dispatch_command(source_sha: str, action: dict[str, Any]) -> list[str]:
    workflow_workers = (
        int(action["workers"]) if action["experiment"] == "f32-parallel" else 1
    )
    return [
        "gh",
        "workflow",
        "run",
        WORKFLOW,
        "--repo",
        REPOSITORY,
        "--ref",
        WORKFLOW_REF,
        "-f",
        f"source_sha={source_sha}",
        "-f",
        f"experiment={action['experiment']}",
        "-f",
        f"file_workers={workflow_workers}",
        "-f",
        f"cache_state={action['cache_state']}",
        "-f",
        f"pair_id={action['pair_id']}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if len(args.source_sha) != 40 or any(
        c not in "0123456789abcdef" for c in args.source_sha
    ):
        parser.error("--source-sha must be a lowercase 40-character SHA")
    if "@sha256:" not in args.image_digest:
        parser.error("--image-digest must be immutable")
    state = load_state(args.state, args.source_sha, args.image_digest)
    if args.record:
        record_evidence(state, json.loads(args.record.read_text()))
        state.pop("dispatched", None)
    action = next_dispatch(state)
    if action is not None and args.dispatch:
        if not args.dry_run:
            subprocess.run(dispatch_command(args.source_sha, action), check=True)
            state["dispatched"] = action
    elif not args.dispatch:
        state.pop("dispatched", None)
    args.state.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
