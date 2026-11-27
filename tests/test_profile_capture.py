"""Exercise durable original-child observation without sending process signals."""

from __future__ import annotations

import importlib.util
import gc
import json
import sys
import tempfile
import os
from contextlib import contextmanager
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "profile_capture", ROOT / "scripts/profile_capture.py"
)
assert SPEC is not None and SPEC.loader is not None
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class ProfileCaptureTests(unittest.TestCase):
    @contextmanager
    def directory(self):
        # Preserve failed originals and partial captures for independent recovery.
        parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        yield tempfile.mkdtemp(prefix="profile-capture-", dir=parent)

    def command(self, source: str) -> list[str]:
        return [sys.executable, "-B", "-c", source]

    def completed(self, folder: Path, source: str = "print('evidence')", **options):
        CAPTURE.launch(folder, self.command(source), timeout=3, **options)
        result = CAPTURE.wait(folder, budget=4)
        self.assertEqual("complete", result["status"], result)
        return result

    def test_completion_and_two_resumes_never_resubmit(self):
        with self.directory() as directory:
            root = Path(directory)
            count = root / "count"
            argv = self.command(
                f"from pathlib import Path; p=Path({str(count)!r}); p.open('a').write('once\\n'); print('evidence')"
            )
            folder = root / "capture"
            CAPTURE.launch(folder, argv, timeout=3)
            result = CAPTURE.wait(folder, budget=4)
            self.assertEqual("complete", result["status"], result)
            self.assertTrue(result["withinDeadline"])
            self.assertEqual(result, CAPTURE.resume(folder, expected_argv=argv))
            self.assertEqual(result, CAPTURE.resume(folder, expected_argv=argv))
            with self.assertRaises(FileExistsError):
                CAPTURE.launch(folder, argv, timeout=3)
            self.assertEqual("once\n", count.read_text())
            self.assertEqual("evidence\n", (folder / "stdout.private.log").read_text())
            self.assertFalse(result["submissionRepeated"])

    def test_partial_timeout_retains_original_then_natural_closure(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            CAPTURE.launch(
                folder,
                self.command(
                    "import time; print('partial',flush=True); time.sleep(2); print('final')"
                ),
                timeout=1,
            )
            first = CAPTURE.wait(folder, budget=1.1)
            self.assertEqual("pending", first["status"], first)
            self.assertTrue(first["deadlineExpired"])
            self.assertIn("partial", (folder / "stdout.private.log").read_text())
            original = first["requestSha256"]
            terminal = CAPTURE.wait(folder, budget=3)
            self.assertEqual("complete", terminal["status"], terminal)
            self.assertFalse(terminal["withinDeadline"])
            self.assertEqual(original, terminal["requestSha256"])
            self.assertFalse(terminal["submissionRepeated"])

    def test_changed_request_is_unresolved_without_replay(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            self.completed(folder)
            path = folder / "request.private.json"
            request = json.loads(path.read_text())
            request["argv"] = self.command("print('changed')")
            path.write_text(json.dumps(request))
            self.assertEqual("request_changed", CAPTURE.resume(folder)["reason"])

    def test_terminal_publication_during_worker_identity_read_is_revalidated(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            original = self.completed(folder)
            terminal = folder / "terminal.private.json"
            unpublished = folder / "unpublished-terminal.private.json"
            terminal.rename(unpublished)
            identity = CAPTURE.identity

            def publish(pid):
                if pid == original["worker"]["pid"]:
                    unpublished.rename(terminal)
                    return None
                return identity(pid)

            with patch.object(CAPTURE, "identity", side_effect=publish):
                self.assertEqual(original, CAPTURE.resume(folder))
            self.assertEqual(original, CAPTURE.resume(folder))
            self.assertFalse(original["submissionRepeated"])

    def test_terminal_race_still_rejects_changed_output(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            original = self.completed(folder)
            terminal = folder / "terminal.private.json"
            unpublished = folder / "unpublished-terminal.private.json"
            terminal.rename(unpublished)
            (folder / "stdout.private.log").write_text("foreign output")

            def publish(_pid):
                unpublished.rename(terminal)
                return None

            with patch.object(CAPTURE, "identity", side_effect=publish):
                self.assertEqual("output_changed", CAPTURE.resume(folder)["reason"])
            self.assertFalse(original["submissionRepeated"])

    def test_changed_expected_command_is_refused(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            self.completed(folder)
            result = CAPTURE.resume(
                folder, expected_argv=self.command("print('different')")
            )
            self.assertEqual("expected_request_changed", result["reason"])

    def test_original_collector_is_frozen_for_future_source_updates(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            result = self.completed(folder)
            frozen = folder / "collector.private.py"
            self.assertEqual(Path(CAPTURE.__file__).read_bytes(), frozen.read_bytes())
            original = CAPTURE.digest

            def changed(path):
                return (
                    "future-source"
                    if Path(path) == Path(CAPTURE.__file__)
                    else original(path)
                )

            with patch.object(CAPTURE, "digest", side_effect=changed):
                self.assertEqual(result, CAPTURE.resume(folder))

    def test_changed_frozen_collector_is_unresolved(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            self.completed(folder)
            (folder / "collector.private.py").write_text("foreign collector")
            self.assertEqual("collector_changed", CAPTURE.resume(folder)["reason"])

    def test_original_worker_is_retained_until_natural_reaping(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            with warnings.catch_warnings(record=True) as observed:
                warnings.simplefilter("always", ResourceWarning)
                CAPTURE.launch(
                    folder, self.command("import time; time.sleep(.2)"), timeout=3
                )
                gc.collect()
                self.assertEqual("complete", CAPTURE.wait(folder, budget=3)["status"])
                gc.collect()
            self.assertEqual(
                [], [item for item in observed if item.category is ResourceWarning]
            )

    def test_changed_output_cannot_qualify_a_terminal_receipt(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            self.completed(folder)
            (folder / "stdout.private.log").write_text("foreign")
            self.assertEqual("output_changed", CAPTURE.resume(folder)["reason"])

    def test_truncation_preserves_bounded_output_and_is_explicit(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            result = self.completed(
                folder,
                "import sys; print('x'*10000); sys.stderr.write('y'*10000)",
                max_bytes=512,
            )
            self.assertTrue(result["truncated"]["stdout"])
            self.assertTrue(result["truncated"]["stderr"])
            self.assertEqual(512, (folder / "stdout.private.log").stat().st_size)
            self.assertEqual(512, (folder / "stderr.private.log").stat().st_size)

    def test_stopped_original_worker_keeps_unresolved_lock(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            CAPTURE.launch(
                folder, self.command("import time; time.sleep(.7)"), timeout=3
            )
            CAPTURE.wait(folder, budget=0.1)
            with patch.object(CAPTURE, "process_state", return_value="T"):
                result = CAPTURE.resume(folder)
            self.assertEqual("original_process_stopped", result["reason"])
            self.assertTrue((folder / "request.private.json").exists())
            self.assertEqual("complete", CAPTURE.wait(folder, budget=3)["status"])

    def test_missing_worker_cannot_be_replaced(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            CAPTURE.launch(
                folder, self.command("import time; time.sleep(.7)"), timeout=3
            )
            CAPTURE.wait(folder, budget=0.1)
            with patch.object(CAPTURE, "identity", return_value=None):
                self.assertEqual(
                    "original_worker_not_verified", CAPTURE.resume(folder)["reason"]
                )
            self.assertEqual("complete", CAPTURE.wait(folder, budget=3)["status"])

    def test_changed_child_identity_preserves_unresolved_capture(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            CAPTURE.launch(
                folder, self.command("import time; time.sleep(.7)"), timeout=3
            )
            CAPTURE.wait(folder, budget=0.1)
            child = json.loads((folder / "child.private.json").read_text())
            original = CAPTURE.identity

            def changed(pid):
                result = original(pid)
                if pid == child["pid"] and result:
                    result = {**result, "startTicks": "foreign"}
                return result

            with patch.object(CAPTURE, "identity", side_effect=changed):
                self.assertEqual(
                    "original_child_not_verified", CAPTURE.resume(folder)["reason"]
                )
            self.assertEqual("complete", CAPTURE.wait(folder, budget=3)["status"])

    def test_concurrent_resumes_do_not_reset_deadline_or_launch(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            CAPTURE.launch(
                folder, self.command("import time; time.sleep(.5)"), timeout=2
            )
            CAPTURE.wait(folder, budget=0.1)
            request = (folder / "request.private.json").read_bytes()
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: CAPTURE.resume(folder), range(12)))
            self.assertTrue(
                all(
                    result["status"] in {"pending", "complete"}
                    and not result["submissionRepeated"]
                    and result["requestSha256"]
                    == CAPTURE.digest(folder / "request.private.json")
                    for result in results
                ),
                results,
            )
            self.assertEqual(request, (folder / "request.private.json").read_bytes())
            self.assertEqual("complete", CAPTURE.wait(folder, budget=3)["status"])

    def test_exact_arguments_never_enter_a_shell(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            value = "literal; $(not-a-command) `also-literal`\nsecond line"
            argv = self.command("import sys; print(sys.argv[1],end='')") + [value]
            CAPTURE.launch(folder, argv, timeout=3)
            self.assertEqual("complete", CAPTURE.wait(folder, budget=4)["status"])
            self.assertEqual(value, (folder / "stdout.private.log").read_text())

    def test_invalid_limits_and_nul_are_rejected_before_submission(self):
        with self.directory() as directory:
            root = Path(directory)
            for index, (argv, timeout) in enumerate(
                [
                    ([], 1),
                    (["bad\0argument"], 1),
                    (self.command("pass"), float("nan")),
                    (self.command("pass"), 0),
                ]
            ):
                folder = root / str(index)
                with self.assertRaises(ValueError):
                    CAPTURE.launch(folder, argv, timeout=timeout)
                self.assertFalse(folder.exists())

    def test_shebang_capture_binds_the_verified_interpreter(self):
        with self.directory() as directory:
            root = Path(directory)
            script = root / "helper"
            script.write_text(
                "#!/usr/bin/env python3\nimport time; time.sleep(.2); print('evidence')\n"
            )
            script.chmod(0o700)
            CAPTURE.launch(root / "capture", [str(script)], timeout=3)
            result = CAPTURE.wait(root / "capture", budget=4)
            self.assertEqual("complete", result["status"], result)
            request = json.loads((root / "capture/request.private.json").read_bytes())
            self.assertEqual(
                request["processExecutableSha256"], result["child"]["executableSha256"]
            )
            self.assertEqual(2, len(request["executionChain"]))

    def test_changed_interpreter_cannot_qualify_original_script_capture(self):
        with self.directory() as directory:
            root = Path(directory)
            script = root / "helper"
            script.write_text(f"#!{sys.executable}\nprint('evidence')\n")
            script.chmod(0o700)
            CAPTURE.launch(root / "capture", [str(script)], timeout=3)
            self.assertEqual(
                "complete", CAPTURE.wait(root / "capture", budget=4)["status"]
            )
            original = CAPTURE.digest
            interpreter = Path(sys.executable).resolve()
            with patch.object(
                CAPTURE,
                "digest",
                side_effect=lambda p: (
                    "changed" if Path(p) == interpreter else original(p)
                ),
            ):
                self.assertEqual(
                    "interpreter_changed", CAPTURE.resume(root / "capture")["reason"]
                )

    def test_unqualified_env_shebang_is_refused_before_submission(self):
        with self.directory() as directory:
            root = Path(directory)
            script = root / "helper"
            script.write_text("#!/usr/bin/env -S python3 -B\nprint('unqualified')\n")
            script.chmod(0o700)
            try:
                CAPTURE.launch(root / "capture", [str(script)], timeout=3)
            except ValueError:
                self.assertFalse((root / "capture").exists())
            else:
                CAPTURE.wait(root / "capture", budget=4)
                self.fail("unqualified env interpreter selection was submitted")

    def test_inplace_tamper_with_restored_mtime_is_rehashed(self):
        with self.directory() as directory:
            path = Path(directory) / "evidence"
            path.write_bytes(b"original")
            before = path.stat()
            original = CAPTURE.digest(path)
            path.write_bytes(b"tampered")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertNotEqual(original, CAPTURE.digest(path))

    def test_fast_native_child_has_explicit_original_execution_evidence(self):
        with self.directory() as directory:
            folder = Path(directory) / "capture"
            CAPTURE.launch(folder, ["/usr/bin/true"], timeout=3)
            result = CAPTURE.wait(folder, budget=4)
            self.assertEqual("complete", result["status"], result)
            self.assertEqual(0, result["code"])
            child = result["child"]
            request = json.loads((folder / "request.private.json").read_bytes())
            if child.get("executionEvidence") == "kernel_spawn_exec":
                self.assertIsNone(child["observedExecutableSha256"])
                self.assertEqual(
                    request["executableSha256"], child["requestedExecutableSha256"]
                )
                self.assertEqual(result["requestSha256"], child["requestSha256"])
            else:
                self.assertEqual(request["executableSha256"], child["executableSha256"])


if __name__ == "__main__":
    unittest.main()
