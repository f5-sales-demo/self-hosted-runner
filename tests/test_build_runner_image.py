import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_runner_image", ROOT / "scripts/build-runner-image.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildDiagnosticsTests(unittest.TestCase):
    def run_child(self, source):
        records = []
        status = build.run_monitored([sys.executable, "-c", source], records.append,
                                     interval=0.01, sample=lambda: {"sample": "test"})
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

    def test_signal_termination_keeps_shell_exit_status(self):
        status, records = self.run_child("import os,signal;os.kill(os.getpid(),signal.SIGTERM)")
        self.assertEqual(status, 143)
        self.assertEqual(records[-1]["childReturnCode"], -signal.SIGTERM)

    def test_periodic_samples_do_not_replace_failed_result(self):
        status, records = self.run_child("import time,sys;time.sleep(.08);sys.exit(17)")
        self.assertEqual(status, 17)
        self.assertTrue(any(item["event"] == "sample" for item in records))
        self.assertEqual(records[-1]["exitCode"], 17)

    def test_proc_status_empty_fields_do_not_discard_memory_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status"
            path.write_text("Name: docker\nVmRSS: 256 kB\nx86_Thread_features:\nVmHWM: 512 kB\n")
            self.assertEqual(build.numbers(path, {"VmRSS", "VmHWM"}),
                             {"VmRSS": 256, "VmHWM": 512})

    def test_missing_telemetry_is_unavailable_not_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(build.numbers(Path(directory) / "missing"),
                             {"unavailable": "FileNotFoundError"})

    def test_parent_signal_is_forwarded_and_retained(self):
        source = """
import importlib.util,json,os,signal,sys,threading
spec=importlib.util.spec_from_file_location('build',sys.argv[1])
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
timer=threading.Timer(.1,lambda:os.kill(os.getpid(),signal.SIGTERM));timer.start()
records=[]
code=build.run_monitored([sys.executable,'-c','import time;time.sleep(5)'],records.append,interval=.01,sample=lambda:{})
print(json.dumps(records));sys.exit(code)
"""
        result = subprocess.run([sys.executable, "-c", source,
                                 str(ROOT / "scripts/build-runner-image.py")],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 143)
        records = json.loads(result.stdout)
        self.assertTrue(any(item["event"] == "signal" and item["signal"] == signal.SIGTERM
                            for item in records))
        self.assertEqual(records[-1]["childReturnCode"], -signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
