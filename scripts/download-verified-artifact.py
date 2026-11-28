#!/usr/bin/env python3
"""Resume one bounded HTTPS artifact transfer and publish only verified bytes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


def download(url: str, expected: str, size: int, output: Path) -> int:
    if (
        urlsplit(url).scheme != "https"
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
        or size <= 0
    ):
        raise ValueError("invalid immutable HTTPS artifact request")
    output = output.absolute()
    if output.is_symlink():
        raise ValueError("artifact output is a symbolic link")
    if output.exists():
        if (
            output.is_file()
            and output.stat().st_size == size
            and hashlib.sha256(output.read_bytes()).hexdigest() == expected
        ):
            return 0
        raise ValueError("existing artifact does not match requested identity")
    capture = output.with_name(output.name + ".capture")
    capture.mkdir(mode=0o700)
    partial = capture / "partial.bin"
    started = time.monotonic()
    deadline = started + 600
    curl = shutil.which("curl")
    if curl is None:
        raise ValueError("curl is unavailable")
    request = {
        "version": 1,
        "url": url,
        "sha256": expected,
        "bytes": size,
        "startedMonotonic": started,
        "deadlineMonotonic": deadline,
        "maximumAttempts": 4,
    }
    (capture / "request.json").write_text(json.dumps(request, indent=2))

    def finish(reason: str, code: int) -> int:
        value = {
            "status": "complete" if code == 0 else "incomplete",
            "reason": reason,
            "code": code,
            "finishedMonotonic": time.monotonic(),
            "sha256": expected,
            "bytes": size,
        }
        (capture / "terminal.json").write_text(json.dumps(value, indent=2))
        print(json.dumps(value))
        return code

    for attempt in range(4):
        remaining = math.floor(deadline - time.monotonic())
        if remaining <= 0:
            return finish("original_transfer_deadline", 1)
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            return finish("artifact_size_exceeded", 1)
        argv = [
            curl,
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--tlsv1.2",
            "--connect-timeout",
            str(min(20, remaining)),
            "--max-time",
            str(min(180, remaining)),
            "--max-filesize",
            str(size),
            "--write-out",
            "%{http_code}",
            "--output",
            str(partial),
        ]
        if offset:
            argv += ["--continue-at", str(offset)]
        argv.append(url)
        with (capture / f"attempt-{attempt}.stderr.log").open("xb") as stderr:
            result = subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=stderr, check=False
            )
        status = result.stdout.decode("ascii", errors="replace").strip()
        received = partial.stat().st_size if partial.exists() else 0
        (capture / f"attempt-{attempt}.json").write_text(
            json.dumps(
                {
                    "argv": argv,
                    "offset": offset,
                    "receivedBytes": received,
                    "httpStatus": status,
                    "code": result.returncode,
                    "finishedMonotonic": time.monotonic(),
                },
                indent=2,
            )
        )
        if result.returncode == 0:
            if received != size:
                return finish("artifact_size_mismatch", 1)
            if hashlib.sha256(partial.read_bytes()).hexdigest() != expected:
                return finish("artifact_digest_mismatch", 1)
            if time.monotonic() > deadline:
                return finish("original_transfer_deadline", 1)
            try:
                os.link(partial, output)
            except FileExistsError:
                return finish("publication_target_changed", 1)
            partial.unlink()
            return finish("verified_immutable_artifact", 0)
        if result.returncode not in (7, 18, 22, 28, 52, 56):
            return finish("nonrecoverable_transport_failure", 1)
        if result.returncode == 22 and status not in (
            "408",
            "429",
            "500",
            "502",
            "503",
            "504",
        ):
            return finish("nonrecoverable_http_failure", 1)
    return finish("transport_attempts_exhausted", 1)


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: download-verified-artifact.py <https-url> <sha256> <bytes> <output>",
            file=sys.stderr,
        )
        return 2
    try:
        return download(sys.argv[1], sys.argv[2], int(sys.argv[3]), Path(sys.argv[4]))
    except (ValueError, OSError):
        print("immutable artifact request or capture is invalid", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
