#!/usr/bin/env python3
"""Collect exact subscription price-sheet entries for runner VM SKUs."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

TARGET_METERS = ("d16ads v5", "f32s v2")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def az_json(arguments: list[str], max_retries: int, retry_base_seconds: float) -> dict:
    last_error = ""
    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                ["az", *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            last_error = "Azure CLI request timed out"
        else:
            if result.returncode == 0:
                payload = json.loads(result.stdout)
                if not isinstance(payload, dict):
                    raise TypeError("Azure CLI response must be a JSON object")
                return payload
            last_error = result.stderr.strip() or result.stdout.strip()
        if attempt + 1 < max_retries:
            time.sleep(min(retry_base_seconds * (2**attempt), 30))
    raise RuntimeError(
        f"Azure CLI request failed after {max_retries} attempts: {last_error}"
    )


def normalize_runner_price(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    properties = entry.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    details = entry.get("meterDetails") or properties.get("meterDetails") or {}
    if not isinstance(details, dict):
        return None
    searchable = " ".join(
        str(details.get(key) or "")
        for key in ("meterName", "meterSubCategory", "meterLocation")
    ).lower()
    if not any(meter in searchable for meter in TARGET_METERS):
        return None
    return {
        "meterName": details.get("meterName"),
        "subCategory": details.get("meterSubCategory"),
        "location": details.get("meterLocation"),
        "unit": entry.get("unitOfMeasure") or properties.get("unitOfMeasure"),
        "unitPrice": entry.get("unitPrice") or properties.get("unitPrice"),
        "currency": entry.get("currencyCode") or properties.get("currencyCode"),
        "partNumber": entry.get("partNumber") or properties.get("partNumber"),
    }


def collect_pages(
    state: dict,
    checkpoint: Path,
    fetch: Callable[[str], dict],
) -> dict:
    while state["next_url"]:
        current_url = state["next_url"]
        payload = fetch(current_url)
        properties = payload.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        entries = (
            properties.get("pricesheets")
            or payload.get("pricesheets")
            or payload.get("value")
            or []
        )
        if not isinstance(entries, list):
            raise TypeError("Azure price-sheet page has no entry list")
        state["pages"] += 1
        state["entries"] += len(entries)
        known = {
            json.dumps(match, sort_keys=True, separators=(",", ":"))
            for match in state["matches"]
        }
        for entry in entries:
            match = normalize_runner_price(entry)
            identity = (
                json.dumps(match, sort_keys=True, separators=(",", ":"))
                if match
                else None
            )
            if match and identity not in known:
                state["matches"].append(match)
                known.add(identity)
        state["next_url"] = payload.get("nextLink") or properties.get("nextLink")
        state["updated_at"] = datetime.now(UTC).isoformat()
        atomic_json(checkpoint, state)
        print(
            f"pages={state['pages']} entries={state['entries']} matches={len(state['matches'])}",
            file=sys.stderr,
            flush=True,
        )
    return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--billing-period", required=True, help="Azure billing period YYYYMM"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--retry-base-seconds", type=float, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if len(args.billing_period) != 6 or not args.billing_period.isdigit():
        raise ValueError("billing period must use YYYYMM")
    if args.max_retries < 1 or args.retry_base_seconds < 0:
        raise ValueError("retry settings must be positive")
    checkpoint = args.checkpoint or args.output.with_suffix(".checkpoint.json")

    account = az_json(
        ["account", "show", "--output", "json"],
        args.max_retries,
        args.retry_base_seconds,
    )
    subscription = account.get("id")
    if not isinstance(subscription, str) or not subscription:
        raise ValueError("Azure account response lacks a subscription ID")
    subscription_hash = hashlib.sha256(subscription.encode()).hexdigest()
    initial_url = (
        f"https://management.azure.com/subscriptions/{subscription}/providers/"
        "Microsoft.Billing/billingPeriods/"
        f"{args.billing_period}/providers/Microsoft.Consumption/pricesheets/"
        "default?$expand=properties/meterDetails&api-version=2023-05-01"
    )

    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (
            state.get("schema_version") != 1
            or state.get("billing_period") != args.billing_period
            or state.get("subscription_sha256") != subscription_hash
        ):
            raise ValueError("price-sheet checkpoint does not match this collection")
    else:
        state = {
            "schema_version": 1,
            "billing_period": args.billing_period,
            "subscription_sha256": subscription_hash,
            "pages": 0,
            "entries": 0,
            "matches": [],
            "next_url": initial_url,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def fetch(url: str) -> dict:
        return az_json(
            ["rest", "--method", "get", "--url", url, "--output", "json"],
            args.max_retries,
            args.retry_base_seconds,
        )

    completed = collect_pages(state, checkpoint, fetch)
    result = {
        key: value
        for key, value in completed.items()
        if key not in {"next_url", "updated_at"}
    }
    result["collected_at"] = datetime.now(UTC).isoformat()
    atomic_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
