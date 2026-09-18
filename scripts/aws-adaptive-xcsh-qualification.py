#!/usr/bin/env python3
"""Resumable, identity-bound dispatcher for AWS xcsh file-worker qualification."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = "f5-sales-demo/xcsh"
WORKFLOW = "compute-benchmark.yml"
WORKFLOW_REF = "main"


def load_state(path: Path, source_sha: str, image_digest: str) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text())
        if state["source_sha"] != source_sha or state["image_digest"] != image_digest:
            raise ValueError("campaign identities differ; start a new state file")
        state.setdefault("controls", [])
        state.setdefault("qualification", [])
        return state
    return {"schema_version": 2, "source_sha": source_sha, "image_digest": image_digest, "production_workers": 0, "controls": [], "probes": [], "qualification": [], "status": "screening"}


def next_probe(state: dict[str, Any]) -> int | None:
    """Choose an adaptive safe probe; unsafe/failed candidates stop upward search."""
    probes = state["probes"]
    bracket = state.get("bracket")
    if isinstance(bracket, list) and len(bracket) == 2:
        lower, upper = (int(value) for value in bracket)
        if upper - lower <= 2:
            return None
        observed = {int(probe["workers"]) for probe in probes}
        midpoint = (lower + upper) // 2
        if midpoint not in observed:
            return midpoint
        return next((worker for worker in range(lower + 1, upper) if worker not in observed), None)
    if not probes:
        return 10
    useful = [p["workers"] for p in probes if p.get("safe") and p.get("improvement", 0) >= .03]
    last = probes[-1]
    if not last.get("safe") or last.get("improvement", 0) < .03:
        lower = max([0, *useful])
        upper = last["workers"]
        state["bracket"] = [lower, upper]
        return next_probe(state)
    if last["workers"] == 10:
        return 20
    if last["workers"] == 20:
        first = next(probe for probe in probes if probe["workers"] == 10)
        if float(last["improvement"]) >= float(first["improvement"]) + .03:
            return 30
        state["bracket"] = [10, 20]
        return next_probe(state)
    if last["workers"] == 30:
        previous = next(probe for probe in probes if probe["workers"] == 20)
        if float(last["improvement"]) >= float(previous["improvement"]) + .03:
            return 32
        state["bracket"] = [20, 30]
        return next_probe(state)
    return None


def record_evidence(state: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Admit only source/image-bound, resource-safe screening evidence."""
    required = {"run_id", "workers", "source_sha", "image_digest", "output_equivalent", "manifest_equivalent", "inventory_complete", "failures", "ooms", "evictions", "restarts", "memory_ratio", "node_pressure", "cpu_throttled", "disk_saturated", "improvement", "typescript_seconds", "critical_path_seconds"}
    missing = required - evidence.keys()
    if missing:
        raise ValueError(f"evidence missing required fields: {sorted(missing)}")
    if evidence["source_sha"] != state["source_sha"] or evidence["image_digest"] != state["image_digest"]:
        raise ValueError("evidence identity does not match frozen campaign")
    safe = (
        bool(evidence["output_equivalent"])
        and bool(evidence["manifest_equivalent"])
        and bool(evidence["inventory_complete"])
        and all(int(evidence[key]) == 0 for key in ("failures", "ooms", "evictions", "restarts"))
        and float(evidence["memory_ratio"]) < .8
        and not any(bool(evidence[key]) for key in ("node_pressure", "cpu_throttled", "disk_saturated"))
    )
    recorded = {"workers": int(evidence["workers"]), "run_id": str(evidence["run_id"]), "safe": safe, "improvement": float(evidence["improvement"]), "memory_ratio": float(evidence["memory_ratio"]), "typescript_seconds": float(evidence["typescript_seconds"]), "critical_path_seconds": float(evidence["critical_path_seconds"]), "role": evidence.get("role", "screening-candidate"), "cache_state": evidence.get("cache_state", "warm"), "pair_id": evidence.get("pair_id")}
    if recorded["role"] == "screening-control":
        state.setdefault("controls", []).append(recorded)
        return
    if recorded["role"] in {"qualification-serial", "qualification-candidate"}:
        state.setdefault("qualification", []).append(recorded)
        return
    state["probes"].append(recorded)
    bracket = state.get("bracket")
    if isinstance(bracket, list) and len(bracket) == 2:
        lower, upper = (int(value) for value in bracket)
        workers = int(evidence["workers"])
        if lower < workers < upper:
            useful = safe and float(evidence["improvement"]) >= .03
            state["bracket"] = [workers, upper] if useful else [lower, workers]
    if not safe:
        state["status"] = "screening-stopped-unsafe"
    elif float(evidence["memory_ratio"]) >= .75:
        workers = int(evidence["workers"])
        lower = max([0, *[int(probe["workers"]) for probe in state["probes"] if int(probe["workers"]) < workers and probe.get("safe") and float(probe.get("improvement", 0)) >= .03]])
        state["bracket"] = [lower, workers]
        state["status"] = "screening-stopped-resource"


def select_worker(state: dict[str, Any]) -> int | None:
    """Select the strongest safe point; within two percent, prefer fewer workers."""
    candidates = [probe for probe in state["probes"] if probe.get("safe") and float(probe.get("improvement", 0)) >= .03]
    if not candidates:
        return None
    best = max(float(probe["improvement"]) for probe in candidates)
    return min(int(probe["workers"]) for probe in candidates if float(probe["improvement"]) >= best - .02)


def percentile95(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot calculate p95 for no samples")
    ordered = sorted(values)
    return ordered[math.ceil(.95 * len(ordered)) - 1]


def evaluate_qualification(state: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the complete matched matrix; only this result may be promotable."""
    evidence = state.get("qualification", [])
    report: dict[str, Any] = {"selected_workers": state.get("selected_workers"), "rollback_workers": 0, "caches": {}, "promotable": False}
    for cache_state in ("cold", "warm"):
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for pair_id in range(1, 6):
            pair = [item for item in evidence if item.get("cache_state") == cache_state and item.get("pair_id") == pair_id]
            serial = [item for item in pair if item.get("role") == "qualification-serial"]
            candidate = [item for item in pair if item.get("role") == "qualification-candidate"]
            if len(serial) != 1 or len(candidate) != 1:
                report["caches"][cache_state] = {"reason": "incomplete-pairs", "promotable": False}
                return report
            if not serial[0]["safe"] or not candidate[0]["safe"]:
                report["caches"][cache_state] = {"reason": "unsafe-evidence", "promotable": False}
                return report
            pairs.append((serial[0], candidate[0]))
        metrics: dict[str, Any] = {}
        for metric in ("typescript_seconds", "critical_path_seconds"):
            baseline = [float(serial[metric]) for serial, _ in pairs]
            candidate = [float(parallel[metric]) for _, parallel in pairs]
            median_baseline = sorted(baseline)[len(baseline) // 2]
            median_candidate = sorted(candidate)[len(candidate) // 2]
            metrics[metric] = {"median_serial": median_baseline, "median_candidate": median_candidate, "p95_serial": percentile95(baseline), "p95_candidate": percentile95(candidate)}
            if median_candidate > median_baseline * .8 or metrics[metric]["p95_candidate"] > metrics[metric]["p95_serial"]:
                report["caches"][cache_state] = {"reason": f"{metric}-gate", "metrics": metrics, "promotable": False}
                return report
        report["caches"][cache_state] = {"metrics": metrics, "promotable": True}
    report["promotable"] = True
    return report


def next_dispatch(state: dict[str, Any], screening_controls: int = 1) -> dict[str, Any] | None:
    """Return exactly one explicit non-polling dispatch action, or None when complete."""
    if state.get("status", "screening").startswith("screening"):
        controls = state.setdefault("controls", [])
        if len(controls) < screening_controls:
            return {"role": "screening-control", "experiment": "d16-serial", "workers": 0, "cache_state": "warm", "pair_id": len(controls) + 1}
        worker = next_probe(state)
        if worker is not None:
            return {"role": "screening-candidate", "experiment": "d16-parallel", "workers": worker, "cache_state": "warm", "pair_id": len(state["probes"]) + 1}
        selected = select_worker(state)
        if selected is None:
            state["status"] = "screening-no-safe-candidate"
            return None
        state["selected_workers"] = selected
        state["status"] = "qualification"
    if state.get("status") != "qualification":
        return None
    selected = int(state["selected_workers"])
    completed = {(item.get("role"), item.get("cache_state"), item.get("pair_id")) for item in state.setdefault("qualification", [])}
    for cache_state in ("cold", "warm"):
        for pair_id in range(1, 6):
            roles = ("qualification-serial", "qualification-candidate") if pair_id % 2 else ("qualification-candidate", "qualification-serial")
            for role in roles:
                if (role, cache_state, pair_id) not in completed:
                    return {"role": role, "experiment": "d16-serial" if role.endswith("serial") else "d16-parallel", "workers": 0 if role.endswith("serial") else selected, "cache_state": cache_state, "pair_id": pair_id}
    state["qualification_report"] = evaluate_qualification(state)
    state["status"] = "promotion-ready" if state["qualification_report"]["promotable"] else "qualification-rejected"
    return None


def dispatch_command(source_sha: str, action: dict[str, Any]) -> list[str]:
    workers = int(action["workers"])
    experiment = str(action["experiment"])
    # The workflow validates workers only for d16-parallel; serial remains zero in its evidence.
    workflow_workers = workers if experiment == "d16-parallel" else 1
    return ["gh", "workflow", "run", WORKFLOW, "--repo", REPOSITORY, "--ref", WORKFLOW_REF,
               "-f", f"source_sha={source_sha}", "-f", f"experiment={experiment}",
               "-f", f"file_workers={workflow_workers}", "-f", f"cache_state={action['cache_state']}", "-f", f"pair_id={action['pair_id']}"]


def dispatch(source_sha: str, action: dict[str, Any], dry_run: bool) -> None:
    command = dispatch_command(source_sha, action)
    if not dry_run:
        subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--screening-controls", type=int, default=1,
                        help="number of warm serial controls before screening candidates (1-3; default: 1)")
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--record", type=Path, help="redacted JSON evidence for one completed run")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if len(args.source_sha) != 40 or any(c not in "0123456789abcdef" for c in args.source_sha):
        parser.error("--source-sha must be a lowercase 40-character SHA")
    if "@sha256:" not in args.image_digest:
        parser.error("--image-digest must be immutable")
    if not 1 <= args.screening_controls <= 3:
        parser.error("--screening-controls must be between 1 and 3")
    state = load_state(args.state, args.source_sha, args.image_digest)
    if args.record:
        record_evidence(state, json.loads(args.record.read_text()))
        state.pop("dispatched", None)
    action = next_dispatch(state, args.screening_controls)
    if action is not None and args.dispatch:
        dispatch(args.source_sha, action, args.dry_run)
        if not args.dry_run:
            state["dispatched"] = action
    elif not args.dispatch:
        state.pop("dispatched", None)
    args.state.write_text(json.dumps(state, indent=2) + "\n")
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
