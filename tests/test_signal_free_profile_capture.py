"""Signal-free source contracts; never execute an unsafe historical profiler."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SignalFreeProfileSourceTests(unittest.TestCase):
    def test_profile_sources_do_not_send_process_signals(self) -> None:
        paths = [
            "scripts/profile_capture.py",
            "scripts/docker-action-profile.py",
            "scripts/build-runner-image.py",
            "scripts/runner-profile.py",
            "tests/test_docker_action_profile.py",
            "tests/test_runner_profile.py",
            "tests/test_build_runner_image.py",
            "tests/test_profile_capture.py",
            "tests/test_profile_observation.py",
            "tests/test_profile_consumers.py",
            "tests/retained_command.py",
            "tests/test_retained_command.py",
            "tests/test_artifact_downloads.py",
            "tests/test_mirror_source_context.py",
        ]
        findings = []
        for name in paths:
            source = ast.parse((ROOT / name).read_text(encoding="utf-8"))
            trees = [source]
            for value in ast.walk(source):
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    try:
                        trees.append(ast.parse(value.value))
                    except SyntaxError:
                        pass
            for node in (node for tree in trees for node in ast.walk(tree)):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Attribute
                ):
                    continue
                attribute = node.func.attr
                if attribute in {
                    "kill",
                    "killpg",
                    "terminate",
                    "send_signal",
                    "pthread_kill",
                }:
                    findings.append((name, node.lineno, "explicit-signal"))
                if attribute in {"run", "check_output", "check_call"} and any(
                    argument.arg == "timeout" for argument in node.keywords
                ):
                    findings.append((name, node.lineno, "implicit-timeout-kill"))
        self.assertEqual(
            [],
            findings,
            "Signal-emitting paths must be repaired before runtime qualification",
        )


if __name__ == "__main__":
    unittest.main()
