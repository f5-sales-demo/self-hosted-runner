from __future__ import annotations

import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import test_artifact_downloads as downloads

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = b"synthetic immutable range-transfer evidence\n" * 128


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        self.server.requests.append(self.headers.get("Range"))
        count = len(self.server.requests)
        offset = int((self.headers.get("Range") or "bytes=0-")[6:].rstrip("-"))
        mode = self.server.mode
        if mode == "target-appears":
            self.server.output.write_bytes(b"foreign concurrent output")
        body = PAYLOAD[offset:]
        if mode == "wrong-digest":
            body = b"x" * len(body)
        code = 206 if offset and mode != "ignore-range" else 200
        self.send_response(code)
        if code == 206:
            self.send_header(
                "Content-Range", f"bytes {offset}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}"
            )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        partial = mode == "always-partial" or (
            mode in ("resume", "ignore-range") and count == 1
        )
        self.wfile.write(body[:32] if partial else body)
        self.wfile.flush()
        if partial:
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_WR)


class VerifiedArtifactDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        downloads.ArtifactDownloadTests.setUpClass()

    def execute(self, mode, *, existing=False):
        directory = Path(
            tempfile.mkdtemp(
                prefix="verified-download-",
                dir=os.environ.get("PROFILE_CAPTURE_TEST_ROOT"),
            )
        )
        output = directory / "artifact.tar"
        if existing:
            output.write_bytes(b"foreign")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.requests = []
        server.mode = mode
        server.output = output
        server.socket = downloads.ArtifactDownloadTests.context.wrap_socket(
            server.socket, server_side=True
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/download-verified-artifact.py"),
                    f"https://127.0.0.1:{server.server_port}/artifact",
                    hashlib.sha256(PAYLOAD).hexdigest(),
                    str(len(PAYLOAD)),
                    str(output),
                ],
                env={
                    **os.environ,
                    "CURL_CA_BUNDLE": str(downloads.ArtifactDownloadTests.certificate),
                },
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        return result, output, server.requests

    def test_managed_transfer_entrypoint_exists(self):
        self.assertTrue((ROOT / "scripts/download-verified-artifact.py").is_file())

    def test_partial_transfer_resumes_exact_offset_and_verifies_full_bytes(self):
        result, output, requests = self.execute("resume")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests, [None, "bytes=32-"])
        self.assertEqual(output.read_bytes(), PAYLOAD)

    def test_wrong_digest_never_publishes_output(self):
        result, output, requests = self.execute("wrong-digest")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(len(requests), 1)
        self.assertTrue(
            (output.with_name(output.name + ".capture") / "partial.bin").is_file()
        )

    def test_range_rejection_is_not_a_silent_full_restart(self):
        result, output, requests = self.execute("ignore-range")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(len(requests), 2)

    def test_exhaustion_retains_partial_evidence_without_final_output(self):
        result, output, requests = self.execute("always-partial")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(len(requests), 4)
        capture = output.with_name(output.name + ".capture")
        request = json.loads((capture / "request.json").read_text())
        self.assertEqual(
            request["deadlineMonotonic"] - request["startedMonotonic"], 600
        )
        for path in capture.glob("attempt-*.json"):
            argv = json.loads(path.read_text())["argv"]
            self.assertLessEqual(int(argv[argv.index("--max-time") + 1]), 180)
            self.assertLessEqual(int(argv[argv.index("--connect-timeout") + 1]), 20)

    def test_foreign_existing_output_is_not_overwritten(self):
        result, output, requests = self.execute("resume", existing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b"foreign")
        self.assertEqual(requests, [])

    def test_concurrent_foreign_output_is_not_replaced(self):
        result, output, requests = self.execute("target-appears")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), b"foreign concurrent output")
        self.assertEqual(len(requests), 1)
        capture = output.with_name(output.name + ".capture")
        self.assertEqual(
            json.loads((capture / "terminal.json").read_text())["reason"],
            "publication_target_changed",
        )
        self.assertTrue((capture / "partial.bin").is_file())
