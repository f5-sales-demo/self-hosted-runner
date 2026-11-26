from __future__ import annotations

import hashlib
import http.server
import os
import re
import shlex
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = b"content-verified immutable archive fixture\n" * 64


class ArtifactServer(http.server.ThreadingHTTPServer):
    mode: str
    requests: int


class ArtifactHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        server = cast(ArtifactServer, self.server)
        server.requests += 1
        if server.mode == "http-error" and server.requests == 1:
            self.send_response(504)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        partial = server.mode == "always-partial" or (
            server.mode == "partial" and server.requests == 1
        )
        self.wfile.write(PAYLOAD[:32] if partial else PAYLOAD)
        self.wfile.flush()
        if partial:
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_WR)


class ArtifactDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="runner-https-fixture-")
        cls.directory = Path(cls.temp.name)
        cls.certificate = cls.directory / "certificate.pem"
        key = cls.directory / "key.pem"
        # A task-owned test credential, trusted only by the one curl subprocess.
        # No host trust store or certificate-verification bypass is used.
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(key),
                "-out",
                str(cls.certificate),
                "-days",
                "1",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.context.load_cert_chain(cls.certificate, key)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def download(
        self, mode: str
    ) -> tuple[subprocess.CompletedProcess[bytes], int, Path]:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        match = re.search(
            r"\bcurl\s+--fail[^\n;]*--output /tmp/dotnet\.tar\.gz[^\n;]*", dockerfile
        )
        self.assertIsNotNone(match, "Exercise the real pinned .NET download invocation")
        assert match is not None
        argv = shlex.split(match.group().rstrip(" \\"))
        target = self.directory / (mode + ".tar.gz")
        argv[argv.index("--output") + 1] = str(target)
        server = ArtifactServer(("127.0.0.1", 0), ArtifactHandler)
        server.mode = mode
        server.requests = 0
        server.socket = self.context.wrap_socket(server.socket, server_side=True)
        argv[-1] = f"https://127.0.0.1:{server.server_port}/artifact"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                timeout=30,
                env={**os.environ, "CURL_CA_BUNDLE": str(self.certificate)},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        return result, server.requests, target

    def test_partial_transfer_recovers_with_complete_verified_content(self) -> None:
        result, requests, target = self.download("partial")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(requests, 2)
        self.assertEqual(
            hashlib.sha256(target.read_bytes()).digest(),
            hashlib.sha256(PAYLOAD).digest(),
        )

    def test_transient_http_failure_recovers(self) -> None:
        result, requests, target = self.download("http-error")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(requests, 2)
        self.assertEqual(target.read_bytes(), PAYLOAD)

    def test_exhausted_partial_transfer_removes_output(self) -> None:
        result, requests, target = self.download("always-partial")
        self.assertEqual(result.returncode, 18)
        self.assertEqual(requests, 4, "At most four total transport attempts")
        self.assertFalse(
            target.exists(), "Incomplete archive must not survive exhaustion"
        )

    def test_every_direct_artifact_download_has_finite_secure_recovery(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        commands = re.findall(r"\bcurl\s+--fail[^\n;]+", dockerfile)
        self.assertGreater(len(commands), 20)
        for command in commands:
            with self.subTest(command=command):
                argv = shlex.split(command.rstrip(" \\"))
                self.assertIn("--proto", argv)
                self.assertIn("--proto-redir", argv)
                self.assertEqual(argv[argv.index("--proto") + 1], "=https")
                self.assertEqual(argv[argv.index("--proto-redir") + 1], "=https")
                for flag, ceiling in (
                    ("--retry", 3),
                    ("--connect-timeout", 20),
                    ("--max-time", 180),
                    ("--retry-max-time", 600),
                ):
                    self.assertIn(flag, argv)
                    value = int(argv[argv.index(flag) + 1])
                    self.assertGreater(value, 0)
                    self.assertLessEqual(value, ceiling)
                self.assertIn("--retry-all-errors", argv)
                self.assertIn("--remove-on-error", argv)
                self.assertNotIn("--insecure", argv)
                self.assertNotIn("-k", argv)
                self.assertNotIn("--continue-at", argv)
