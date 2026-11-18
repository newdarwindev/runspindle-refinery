"""Composed signed local export through real temporary synthetic TLS, not Langfuse."""

import importlib
import json
import socket
import ssl
import subprocess
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from refinery import dataset_contract as c
from refinery.dataset_export import DatasetProjection, Export
from refinery.dataset_materialization import DatasetRegistry
from refinery.evaluation import Halt
from refinery.langfuse_http import DatasetHTTP
from refinery.langfuse_projection import HTTP
from refinery.storage import Store


# Capture before the signed fixture installs its default deny-all patch.
class ListenerOnly(socket.socket):
    def __init__(
        self,
        family: int = socket.AF_INET,
        type: int = socket.SOCK_STREAM,
        proto: int = 0,
        fileno: int | None = None,
    ) -> None:
        if family != socket.AF_INET or type != socket.SOCK_STREAM or proto != 0:
            raise AssertionError("only IPv4 TCP synthetic listener permitted")
        super().__init__(family, type, proto, fileno=fileno)

    def sendto(self, *args: Any, **kwargs: Any) -> int:
        raise AssertionError("parent datagram network forbidden")

    def bind(self, address: Any) -> None:
        if not isinstance(address, tuple) or address[0] != "127.0.0.1":
            raise AssertionError("only synthetic loopback listener permitted")
        super().bind(address)

    def connect(self, address: Any) -> None:
        raise AssertionError("parent outbound network forbidden")

    def connect_ex(self, address: Any) -> int:
        raise AssertionError("parent outbound network forbidden")


class DatasetExportTLSTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture: Any = importlib.import_module(
            "test_dataset_export"
        ).DatasetExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        root = Path(self.fixture.h.path).parent
        cert, key = root / "tls-cert.pem", root / "tls-key.pem"
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
        self.remote: Any = None
        self.posts = 0
        self.lose_ack = False
        self.hidden = False
        self.wrong: str | None = None
        self.withdraw_after_post = False
        self.errors: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def respond(self, status: int, value: Any) -> None:
                raw = c.canonical(value)
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                owner.seen.append(
                    ("GET", self.path, self.headers.get("Authorization"), None)
                )
                if (
                    self.path
                    == "/api/public/v2/datasets/" + owner.destination.dataset_name
                ):
                    self.respond(
                        200,
                        dict(
                            projectId="wrong"
                            if owner.wrong == "project"
                            else owner.destination.project_id,
                            id=owner.destination.dataset_id,
                            name=owner.destination.dataset_name,
                            metadata=dict(
                                runspindle_dataset_split="wrong"
                                if owner.wrong == "split"
                                else owner.destination.split
                            ),
                        ),
                    )
                elif self.path == "/api/public/dataset-items/" + owner.export.key:
                    self.respond(
                        404, {}
                    ) if owner.hidden or owner.remote is None else self.respond(
                        200, owner.remote
                    )
                else:
                    owner.errors.append("unexpected GET endpoint")
                    self.respond(400, {})

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                owner.seen.append(
                    ("POST", self.path, self.headers.get("Authorization"), raw)
                )
                if self.path != "/api/public/dataset-items":
                    owner.errors.append("unexpected POST endpoint")
                    self.respond(400, {})
                    return
                owner.posts += 1
                owner.remote = dict(
                    json.loads(raw),
                    datasetId=owner.destination.dataset_id,
                    sourceTraceId=None,
                    sourceObservationId=None,
                    mediaReferences=[],
                )
                if owner.withdraw_after_post:
                    registry = DatasetRegistry(owner.fixture.h.path)
                    try:
                        registry.withdraw(
                            owner.fixture.item["item_id"],
                            approve=lambda *_: True,
                            permission_ref="synthetic-withdrawal",
                        )
                    finally:
                        registry.close()
                if owner.lose_ack:
                    self.close_connection = True
                    return
                self.respond(200, {"id": owner.remote["id"]})

        listener_patch = patch.object(socket, "socket", ListenerOnly)
        listener_patch.start()
        self.addCleanup(listener_patch.stop)
        server = HTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        server.timeout = 3
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
            self.assertEqual(self.errors, [])

        self.addCleanup(cleanup)
        self.destination = replace(
            self.fixture.dest, origin="https://127.0.0.1:" + str(server.server_port)
        )
        self.export = self.make()
        self.http = DatasetHTTP(
            self.destination.origin, "Basic c3ludGhldGljOnRlc3Q=", cert.read_text()
        )
        # Existing protocol declares writable origin; concrete frozen transport
        # intentionally provides the same read-only value. No runtime wrapper.
        self.projection = DatasetProjection(cast(HTTP, self.http), self.destination)

    def make(self, source: Any = None) -> Export:
        source = source or self.fixture.source
        return Export(
            source.store,
            self.fixture.h.registry,
            source,
            self.fixture.item["item_id"],
            self.destination,
            approve=self.fixture.approve,
            permission_ref="export-grant",
        )

    def test_exact_signed_materialization_through_real_tls_and_current_grant(
        self,
    ) -> None:
        expected = c.canonical(self.export.body())
        with patch.dict(
            "os.environ", HTTPS_PROXY="http://127.0.0.1:1", LANGFUSE_SECRET_KEY="unused"
        ):
            self.assertEqual(self.export.dispatch(self.projection), "delivered")
        post = [row for row in self.seen if row[0] == "POST"]
        self.assertEqual(
            post,
            [("POST", "/api/public/dataset-items", self.http.authorization, expected)],
        )
        self.assertIsNotNone(self.export.record("readback"))
        self.assertEqual(self.export.dispatch(self.projection), "delivered")
        self.assertEqual(self.posts, 1)
        self.fixture.allow = False
        before = len(self.seen)
        with self.assertRaises(ValueError):
            self.make().dispatch(self.projection)
        self.assertEqual(len(self.seen), before)
        self.assertIsNotNone(self.remote)

    def test_accepted_post_lost_ack_reopen_hidden_then_exact_readback_no_repost(
        self,
    ) -> None:
        self.lose_ack = True
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            self.export.dispatch(self.projection)
        self.assertEqual(self.posts, 1)
        self.assertIsNotNone(self.remote)
        self.assertIsNotNone(self.export.record("attempt"))
        self.assertIsNone(self.export.record("readback"))
        with Store(self.fixture.source.store.path) as store:
            reopened = self.make(replace(self.fixture.source, store=store))
            self.hidden = True
            self.assertEqual(reopened.dispatch(self.projection), "uncertain")
            self.hidden = False
            self.assertEqual(reopened.dispatch(self.projection), "delivered")
            self.assertIsNotNone(reopened.record("readback"))
        self.assertEqual(self.posts, 1)

    def test_registry_withdrawal_after_actual_post_denies_settlement_not_erasure(
        self,
    ) -> None:
        self.withdraw_after_post = True
        with self.assertRaisesRegex(ValueError, "dataset item withdrawn"):
            self.export.dispatch(self.projection)
        with self.assertRaisesRegex(ValueError, "dataset item withdrawn"):
            self.fixture.h.registry.available(self.fixture.item["item_id"])
        self.assertEqual(self.posts, 1)
        retained = c.canonical(self.remote)
        self.assertIsNone(self.export.record("readback"))
        with self.assertRaises(ValueError):
            self.make().dispatch(self.projection)
        self.assertEqual(c.canonical(self.remote), retained)
        self.assertEqual(self.posts, 1)

    def test_source_withdrawal_and_local_tombstone_block_reconciliation(self) -> None:
        self.lose_ack = True
        with self.assertRaises(ValueError):
            self.export.dispatch(self.projection)
        retained = c.canonical(self.remote)
        self.fixture.h.h.h.invalidate_target("message_deleted")
        with self.assertRaises((ValueError, Halt)):
            self.make().dispatch(self.projection)
        self.assertIsNone(self.export.record("readback"))
        self.export.revoke(approve=self.fixture.approve, permission_ref="export-grant")
        with self.assertRaisesRegex(ValueError, "locally revoked"):
            self.make().dispatch(self.projection)
        self.assertEqual(c.canonical(self.remote), retained)
        self.assertEqual(self.posts, 1)

    def test_wrong_project_and_split_deny_before_actual_post(self) -> None:
        for wrong in ("project", "split"):
            self.wrong = wrong
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                self.export.dispatch(self.projection)
        self.assertEqual(self.posts, 0)
        self.assertIsNone(self.export.record("attempt"))

    def test_listener_exception_is_narrow(self) -> None:
        for family, kind in (
            (socket.AF_INET6, socket.SOCK_STREAM),
            (socket.AF_INET, socket.SOCK_DGRAM),
        ):
            with self.assertRaises(AssertionError):
                ListenerOnly(family, kind)
        with ListenerOnly() as listener:
            with self.assertRaises(AssertionError):
                listener.bind(("0.0.0.0", 0))
            with self.assertRaises(AssertionError):
                listener.connect(("127.0.0.1", 1))
            with self.assertRaises(AssertionError):
                listener.connect_ex(("127.0.0.1", 1))
            with self.assertRaises(AssertionError):
                listener.sendto(b"x", ("127.0.0.1", 1))
        self.assertEqual(self.seen, [])
