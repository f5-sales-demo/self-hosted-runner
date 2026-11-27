"""Run real test commands with retained deadlines and natural child closure."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import profile_capture as capture


def run_command(argv, folder, timeout, *, env=None, text=False, check=False):
    folder = Path(folder)
    context = (
        patch.dict(os.environ, env, clear=True) if env is not None else nullcontext()
    )
    with context:
        capture.launch(folder, argv, timeout=timeout)
    result = capture.wait(folder, budget=timeout)
    if (
        result["status"] != "complete"
        or not result["withinDeadline"]
        or any(result["truncated"].values())
    ):
        raise capture.ObservationIncomplete("test_command_capture_incomplete")
    stdout = (folder / "stdout.private.log").read_bytes()
    stderr = (folder / "stderr.private.log").read_bytes()
    if text:
        stdout, stderr = stdout.decode(), stderr.decode()
    completed = subprocess.CompletedProcess(argv, result["code"], stdout, stderr)
    if check:
        completed.check_returncode()
    return completed
