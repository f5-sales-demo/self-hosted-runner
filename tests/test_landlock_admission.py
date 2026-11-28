from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "scripts" / "require-landlock-abi.sh"
ENTRYPOINT = ROOT / "scripts" / "runner-entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"


class LandlockAdmissionTests(unittest.TestCase):
    def run_policy(self, body: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "probe"
            probe.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
            probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
            return subprocess.run(
                ["bash", str(POLICY), str(probe)],
                text=True,
                capture_output=True,
                check=False,
                env={"PATH": os.environ["PATH"]},
            )

    def test_accepts_landlock_abi_two_and_newer(self) -> None:
        for abi in (2, 6):
            with self.subTest(abi=abi):
                result = self.run_policy(f"printf '{abi}\\n'")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("", result.stderr)
                self.assertEqual(
                    f"runner admission accepted: Landlock ABI {abi}\n", result.stdout
                )

    def test_rejects_landlock_abi_one_with_actionable_diagnostic(self) -> None:
        result = self.run_policy("printf '1\\n'")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("observed ABI 1", result.stderr)
        self.assertIn("ABI 2 or newer", result.stderr)
        self.assertIn("HWE", result.stderr)

    def test_rejects_unavailable_or_blocked_probe(self) -> None:
        result = self.run_policy("printf 'operation not permitted\\n' >&2; exit 1")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("unavailable or blocked", result.stderr)
        self.assertIn("ABI 2 or newer", result.stderr)
        self.assertNotIn("operation not permitted", result.stderr)

    def test_rejects_malformed_probe_output(self) -> None:
        result = self.run_policy("printf 'unknown\\n'")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid ABI result", result.stderr)
        self.assertIn("ABI 2 or newer", result.stderr)

    def test_entrypoint_checks_before_reading_registration_token(self) -> None:
        entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
        policy_call = entrypoint.index("/usr/local/bin/require-landlock-abi")
        token_read = entrypoint.index("IFS= read -r registration_token")
        registration = entrypoint.index("./config.sh")
        self.assertLess(policy_call, token_read)
        self.assertLess(policy_call, registration)

    def test_every_arc_runner_checks_landlock_before_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            gate = folder / "gate"
            registration = folder / "registration"
            calls = folder / "calls"
            gate.write_text(
                '#!/bin/sh\nprintf "gate\\n" >> "$ADMISSION_CALLS"\nexit "$ADMISSION_CODE"\n'
            )
            registration.write_text(
                '#!/bin/sh\nprintf "registration\\n" >> "$ADMISSION_CALLS"\n'
            )
            gate.chmod(0o700)
            registration.chmod(0o700)
            for profile in ("socketless", "compute", "container-build"):
                values = yaml.safe_load(
                    (ROOT / f"arc/{profile}-values.yaml").read_text()
                )
                runner = next(
                    item
                    for item in values["template"]["spec"]["containers"]
                    if item["name"] == "runner"
                )
                self.assertEqual(runner["command"], ["/bin/bash", "-c"])
                self.assertEqual(
                    runner["args"],
                    ["/usr/local/bin/require-landlock-abi && exec /home/runner/run.sh"],
                )
                script = (
                    runner["args"][0]
                    .replace(
                        "/usr/local/bin/require-landlock-abi", shlex.quote(str(gate))
                    )
                    .replace("/home/runner/run.sh", shlex.quote(str(registration)))
                )
                for code in (0, 1):
                    with self.subTest(profile=profile, admission=code):
                        calls.unlink(missing_ok=True)
                        result = subprocess.run(
                            [*runner["command"], script],
                            env={
                                **os.environ,
                                "ADMISSION_CALLS": str(calls),
                                "ADMISSION_CODE": str(code),
                            },
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, code, result.stderr)
                        self.assertEqual(
                            calls.read_text().splitlines(),
                            ["gate", "registration"] if code == 0 else ["gate"],
                        )

    def test_image_builds_and_installs_probe_and_policy(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "COPY --chown=root:root scripts/landlock-abi.c /tmp/landlock-abi.c",
            dockerfile,
        )
        self.assertIn(
            "COPY --chown=root:root scripts/require-landlock-abi.sh "
            "/usr/local/bin/require-landlock-abi",
            dockerfile,
        )
        self.assertIn(
            "cc -O2 -Wall -Wextra -Werror /tmp/landlock-abi.c "
            "-o /usr/local/bin/landlock-abi",
            dockerfile,
        )


if __name__ == "__main__":
    unittest.main()
