import importlib.util
import os
from contextlib import contextmanager
from pathlib import Path
import signal
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location(
    "build_runner_image", ROOT / "scripts/build-runner-image.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@contextmanager
def preserved_directory():
    parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    yield tempfile.mkdtemp(prefix="profiler-regression-", dir=parent)


class BuildDiagnosticsTests(unittest.TestCase):
    def run_child(self, source):
        records = []
        status = build.run_monitored(
            [sys.executable, "-c", source],
            records.append,
            interval=0.01,
            sample=lambda: {"sample": "test"},
        )
        return status, records

    def test_success_requires_successful_child(self):
        status, records = self.run_child("pass")
        self.assertEqual(status, 0)
        self.assertEqual(records[0]["event"], "start")
        self.assertEqual(records[-1]["exitCode"], 0)

    def test_explicit_143_is_not_hidden_by_diagnostics(self):
        status, records = self.run_child("import sys;sys.exit(143)")
        self.assertEqual(status, 143)
        self.assertEqual(records[-1]["childReturnCode"], 143)

    def test_signal_status_mapping_without_sending_a_signal(self):
        # An externally terminated child is represented by its retained receipt.
        self.assertEqual(143, build.shell_exit_status(-signal.SIGTERM))

    def test_periodic_samples_do_not_replace_failed_result(self):
        with preserved_directory() as directory:
            marker = Path(directory) / "sample-observed"
            records = []

            def sample():
                if records:
                    marker.touch()
                return {"sample": "test"}

            source = (
                f"from pathlib import Path; import sys,time; marker=Path({str(marker)!r}); "
                "deadline=time.monotonic()+3\n"
                "while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                "sys.exit(17)"
            )
            status = build.run_monitored(
                [sys.executable, "-c", source],
                records.append,
                interval=0.01,
                sample=sample,
            )
            self.assertEqual(status, 17)
            self.assertTrue(any(item["event"] == "sample" for item in records))
            self.assertEqual(records[-1]["exitCode"], 17)

    def test_proc_status_empty_fields_do_not_discard_memory_values(self):
        with preserved_directory() as directory:
            path = Path(directory) / "status"
            path.write_text(
                "Name: docker\nVmRSS: 256 kB\nx86_Thread_features:\nVmHWM: 512 kB\n"
            )
            self.assertEqual(
                build.numbers(path, {"VmRSS", "VmHWM"}), {"VmRSS": 256, "VmHWM": 512}
            )

    def test_missing_telemetry_is_unavailable_not_zero(self):
        with preserved_directory() as directory:
            self.assertEqual(
                build.numbers(Path(directory) / "missing"),
                {"unavailable": "FileNotFoundError"},
            )

    def test_registered_parent_handler_records_cancellation_without_forwarding(self):
        from unittest.mock import patch
        import threading

        handlers = {}
        registered = threading.Event()

        def register(signum, handler):
            old = handlers.get(signum)
            handlers[signum] = handler
            if signum == signal.SIGTERM and callable(handler):
                registered.set()
            return old

        def request():
            if registered.wait(5):
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        with patch.object(build.signal, "signal", side_effect=register):
            timer = threading.Thread(target=request)
            timer.start()
            status, records = self.run_child("import time; time.sleep(.2)")
            timer.join()
        self.assertEqual(143, status)
        self.assertTrue(
            any(
                item["event"] == "signal" and item["signal"] == signal.SIGTERM
                for item in records
            )
        )
        self.assertEqual("cancelled", records[-1]["result"])
        self.assertFalse(
            any(item.get("childReturnCode") == -signal.SIGTERM for item in records)
        )


if __name__ == "__main__":
    unittest.main()
