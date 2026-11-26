from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PackageRetryContractTests(unittest.TestCase):
    def run_loop(self, failure):
        dockerfile = (ROOT / "Dockerfile").read_text()
        loop = re.search(r"&& for attempt in .*?    done", dockerfile, re.S)
        self.assertIsNotNone(loop)
        script = "set -eu\npackages=libsystemd-dev\n" + loop.group(0).removeprefix("&& ").replace("\\\n", "") + "\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / "calls.jsonl"
            calls.write_text("")
            apt = root / "apt-get"
            apt.write_text("#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\nwith open(os.environ['PACKAGE_TEST_CALLS'],'a') as f: f.write(json.dumps(args)+'\\n')\nif args[-1:] == ['update']: sys.exit(0)\nif '--no-download' in args and os.environ['PACKAGE_TEST_FAILURE'].startswith('network'): sys.exit(0)\nif '--print-uris' in args and os.environ['PACKAGE_TEST_FAILURE']!='resolve': sys.exit(0)\nprint('E: Unable to correct problems, you have held broken packages.',file=sys.stderr)\nsys.exit(100)\n")
            apt.chmod(0o700)
            for name in ["install", "rm", "find", "sleep"]:
                path = root / name
                path.write_text("#!/bin/sh\nexit 0\n")
                path.chmod(0o700)
            download = root / "xargs"
            download.write_text("#!/usr/bin/python3\nimport os,sys\nfrom pathlib import Path\np=Path(os.environ['PACKAGE_TEST_XARGS'])\nn=len(p.read_text().splitlines()) if p.exists() else 0\nwith p.open('a') as f: f.write(str(n)+'\\n')\nmode=os.environ['PACKAGE_TEST_FAILURE']\nsys.exit(1 if mode=='network-always' or (mode=='network-once' and n==0) else 0)\n")
            download.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"], PACKAGE_TEST_CALLS=str(calls), PACKAGE_TEST_FAILURE=failure, TMPDIR=str(root), PACKAGE_TEST_XARGS=str(root / "downloads.jsonl"))
            result = subprocess.run(["/bin/sh", "-c", script], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            commands = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertNotIn("Syntax error", result.stderr)
            self.assertTrue(commands, result.stderr)
        return result, commands

    def test_dependency_conflict_stops_before_download_and_unchanged_retry(self):
        result, commands = self.run_loop("resolve")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum("--print-uris" in command for command in commands), 1)
        self.assertEqual(sum("--no-download" in command for command in commands), 0)

    def test_deterministic_install_failure_is_not_retried(self):
        result, commands = self.run_loop("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum("--print-uris" in command for command in commands), 1)
        self.assertEqual(sum("--no-download" in command for command in commands), 1)

    def test_transient_download_failure_recovers_on_bounded_next_attempt(self):
        result, commands = self.run_loop("network-once")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum("--print-uris" in command for command in commands), 2)
        self.assertEqual(sum("--no-download" in command for command in commands), 1)

    def test_download_failures_stop_at_existing_eight_attempt_bound(self):
        result, commands = self.run_loop("network-always")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum("--print-uris" in command for command in commands), 8)
        self.assertEqual(sum("--no-download" in command for command in commands), 0)
