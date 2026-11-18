"""Loopback TLS protocol tests, explicitly NOT an actual Langfuse server."""

import json
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.langfuse_http import LangfuseHTTP


class HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        cert, key = root / "cert.pem", root / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        self.seen: list[Any] = []
        self.mode = "ok"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.seen.append((self.path, self.headers["Authorization"], body))
                self.reply()

            def do_GET(self) -> None:
                owner.seen.append(self.path)
                self.reply()

            def reply(self) -> None:
                if owner.mode == "slow":
                    time.sleep(2)
                if owner.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://unauthorized.invalid/")
                    self.end_headers()
                    return
                raw = b"x" * 65537 if owner.mode == "large" else b'{"id":"synthetic"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ssl.SSLError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            server.shutdown()
            server.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())

        self.addCleanup(cleanup)
        self.origin = "https://127.0.0.1:" + str(server.server_port)
        self.http = LangfuseHTTP(
            self.origin, "Basic c3ludGhldGljOnRlc3Q=", cert.read_text()
        )

    def request(self, **changes: Any) -> Any:
        args: dict[str, Any] = dict(
            method="POST",
            path="/api/public/scores",
            body=b"{}",
            timeout=3,
            max_bytes=65536,
        )
        args.update(changes)
        return self.http.request(**args)

    def test_real_tls_request_no_proxy_and_bounded_response(self) -> None:
        with patch.dict(
            "os.environ",
            HTTPS_PROXY="http://127.0.0.1:1",
            HTTP_PROXY="http://127.0.0.1:1",
        ):
            result = self.request()
        self.assertEqual(result.status, 200)
        self.assertEqual(json.loads(result.body), {"id": "synthetic"})
        self.assertEqual(
            self.seen, [("/api/public/scores", "Basic c3ludGhldGljOnRlc3Q=", b"{}")]
        )
        self.assertNotIn("c3ludGhldGlj", repr(self.http))

    def test_redirect_not_followed(self) -> None:
        self.mode = "redirect"
        self.assertEqual(self.request().status, 302)
        self.assertEqual(len(self.seen), 1)

    def test_oversized_and_deadline_fail_without_retry(self) -> None:
        self.mode = "large"
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            self.request()
        self.mode = "slow"
        start = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "unconfirmed"):
            self.request(timeout=1)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(len(self.seen), 2)

    def test_untrusted_tls_and_unsafe_requests_fail(self) -> None:
        without_ca = LangfuseHTTP(self.origin, "Basic c3ludGhldGljOnRlc3Q=")
        with self.assertRaises(ValueError):
            without_ca.request(
                "POST", "/api/public/scores", b"{}", timeout=3, max_bytes=65536
            )
        for kwargs in (
            {"path": "https://elsewhere.invalid/"},
            {"method": "DELETE"},
            {"body": b"x" * 65537},
            {"timeout": 11},
            {"max_bytes": 65537},
        ):
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValueError):
                    self.request(**kwargs)
        with self.assertRaises(ValueError):
            LangfuseHTTP("https://remote.example", "Basic token", self.http.ca_pem)
        with self.assertRaises(ValueError):
            LangfuseHTTP("http://127.0.0.1", "Basic token")
        self.assertEqual(self.seen, [])
