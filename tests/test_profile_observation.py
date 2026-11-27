"""Original profiler lifetime, cooperative cancellation and no-replay contracts."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "profile_capture", ROOT / "scripts/profile_capture.py"
)
assert SPEC is not None and SPEC.loader is not None
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class ProfileObservationTests(unittest.TestCase):
    @contextmanager
    def directory(self):
        parent = os.environ.get("PROFILE_CAPTURE_TEST_ROOT")
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        yield Path(tempfile.mkdtemp(prefix="profile-observation-", dir=parent))

    def command(self, source):
        return [sys.executable, "-B", "-c", source]

    def observation(self, root, timeout=3):
        return CAPTURE.Observation(
            root / "capture",
            {"kind": "synthetic-test"},
            timeout,
            sources=[Path(CAPTURE.__file__)],
        )

    def output(self, root):
        output = root / "profile.json"
        output.write_text("{}")
        return output

    def test_complete_lifetime_is_bound_to_original_commands_and_output(self):
        with self.directory() as root:
            observer = self.observation(root)
            self.assertEqual(
                b"evidence\n", observer.run(self.command("print('evidence')"))
            )
            observed = time.monotonic()
            self.assertTrue(observer.close(budget=3))
            value = observer.finish(
                self.output(root), code=0, result="completed", observed_at=observed
            )
            self.assertEqual(0, value["code"])
            self.assertEqual("completed", value["result"])
            self.assertEqual(
                value,
                CAPTURE.Observation.inspect(
                    root / "capture", {"kind": "synthetic-test"}
                ),
            )
            self.assertEqual(
                value,
                CAPTURE.Observation.inspect(
                    root / "capture", {"kind": "synthetic-test"}
                ),
            )
            with self.assertRaises(FileExistsError):
                self.observation(root)

    def test_helpers_share_the_original_deadline(self):
        with self.directory() as root:
            observer = self.observation(root, timeout=1)
            request = (root / "capture/observation.private.json").read_bytes()
            observer.run(self.command("import time; time.sleep(.55)"))
            with self.assertRaises(CAPTURE.ObservationIncomplete):
                observer.run(self.command("import time; time.sleep(.65)"))
            self.assertEqual(
                request, (root / "capture/observation.private.json").read_bytes()
            )
            self.assertTrue(observer.close(budget=3))
            result = observer.finish(self.output(root), code=0, result="completed")
            self.assertEqual("unresolved", result["result"])
            self.assertNotEqual(0, result["code"])

    def test_cooperative_cancellation_is_bound_and_children_close_naturally(self):
        with self.directory() as root:
            observer = self.observation(root)
            observer.start(self.command("import time; time.sleep(.2)"))
            self.assertFalse(observer.cancel_requested())
            CAPTURE.Observation.request_cancel(
                root / "capture", {"kind": "synthetic-test"}
            )
            self.assertTrue(observer.cancel_requested())
            self.assertTrue(observer.close(budget=3))
            result = observer.finish(self.output(root), code=143, result="cancelled")
            self.assertEqual("cancelled", result["result"])
            self.assertEqual(143, result["code"])
            self.assertFalse(result["submissionRepeated"])

    def test_foreign_cancellation_control_is_refused(self):
        with self.directory() as root:
            observer = self.observation(root)
            with self.assertRaises(CAPTURE.ObservationIncomplete):
                CAPTURE.Observation.request_cancel(
                    root / "capture", {"kind": "foreign"}
                )
            self.assertFalse((root / "capture/cancel.private.json").exists())
            observer.run(self.command("pass"))
            self.assertTrue(observer.close(budget=3))

    def test_changed_config_and_output_cannot_resume_as_complete(self):
        with self.directory() as root:
            observer = self.observation(root)
            observer.run(self.command("pass"))
            self.assertTrue(observer.close(budget=3))
            output = self.output(root)
            observer.finish(output, code=0, result="completed")
            changed = CAPTURE.Observation.inspect(root / "capture", {"kind": "foreign"})
            self.assertEqual("observation_config_changed", changed["reason"])
            output.write_text("foreign output")
            changed = CAPTURE.Observation.inspect(
                root / "capture", {"kind": "synthetic-test"}
            )
            self.assertEqual("observation_output_changed", changed["reason"])

    def test_late_natural_closure_does_not_promote_an_unresolved_outcome(self):
        with self.directory() as root:
            observer = self.observation(root, timeout=1)
            observer.start(
                self.command("import time; print('partial',flush=True); time.sleep(2)")
            )
            self.assertFalse(observer.close(budget=0.1))
            outcome = observer.finish(self.output(root), code=0, result="completed")
            self.assertNotEqual(0, outcome["code"])
            self.assertEqual("unresolved", outcome["result"])
            self.assertTrue(observer.close(budget=3))
            self.assertEqual(
                outcome,
                CAPTURE.Observation.inspect(
                    root / "capture", {"kind": "synthetic-test"}
                ),
            )

    def test_original_observer_owner_is_required_while_pending(self):
        with self.directory() as root:
            observer = self.observation(root)
            observer.start(self.command("import time; time.sleep(.2)"))
            binding = root / "capture/observer-binding.private.json"
            original = binding.read_bytes()
            value = json.loads(original)
            value["owner"]["startTicks"] = "foreign"
            binding.write_text(json.dumps(value))
            result = CAPTURE.Observation.inspect(
                root / "capture", {"kind": "synthetic-test"}
            )
            self.assertEqual("original_observer_not_verified", result["reason"])
            binding.write_bytes(original)
            self.assertTrue(observer.close(budget=3))


if __name__ == "__main__":
    unittest.main()
