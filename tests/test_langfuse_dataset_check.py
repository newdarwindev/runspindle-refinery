"""Source-only client/config negatives with synthetic injected protocol peer."""

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.dataset_export import Destination
from refinery.langfuse_projection import Response
from tools import langfuse_dataset_check as check


class DatasetClientTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        ca, key = self.root / "ca.pem", self.root / "key.pem"
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
                str(ca),
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
        auth = self.root / "auth"
        auth.write_text("Basic c3ludGhldGljOnRlc3Q=")
        auth.chmod(0o600)
        self.path = self.root / "private.json"
        self.value: dict[str, Any] = dict(
            schema=1,
            origin="https://127.0.0.1:8443",
            authorization_file=str(auth),
            project_id="project",
            dataset_id="dataset",
            dataset_name="existing_dataset",
            dataset_split="train",
            ca_file=str(ca),
            ca_sha256=hashlib.sha256(ca.read_bytes()).hexdigest(),
            backend=dict(check.BACKEND),
        )
        self.write(self.value)

    def write(self, value: Any) -> None:
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def remote(self, *args: Any) -> Any:
        remote = importlib.import_module("test_dataset_export").HTTP(
            Destination(
                self.value["origin"], "project", "dataset", "existing_dataset", "train"
            )
        )
        remote.origin = self.value["origin"]
        return remote

    def test_qualify_synthetic_peer_post_once_reopen_and_denials(self) -> None:
        config = check.configuration(self.path)
        remote = self.remote()
        with patch.object(check, "DatasetHTTP", return_value=remote):
            result = check.qualify(config)
        self.assertEqual(
            (
                result["post_entries"],
                result["accepted_posts"],
                result["injected_lost_ack"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(remote.posts, 1)
        self.assertEqual(result["get_entries"], 4)
        self.assertEqual(result["readback_attempts"], 1)
        self.assertTrue(result["registry_withdrawal_confirmed"])
        self.assertEqual(result["remote_cleanup"], "not_attempted_no_erasure_claim")
        self.assertIn("runner_pinned_image", result["server_identity"])
        encoded = json.dumps(result)
        self.assertNotIn(config["authorization"], encoded)
        self.assertNotIn(str(self.root), encoded)
        for file in (
            "refinery/dataset_export.py",
            "tests/test_feedback_collection.py",
            "tests/test_dataset_export.py",
            "tools/langfuse_dataset_check.py",
        ):
            self.assertIn(file, result["source_files"])

    def test_actual_acceptance_required_before_fault_and_scrubbed_failure(self) -> None:
        config = check.configuration(self.path)
        remote = self.remote()
        remote.wrong = True
        with (
            patch.object(check, "DatasetHTTP", return_value=remote),
            self.assertRaises(check.QualificationFailure) as caught,
        ):
            check.qualify(config)
        self.assertEqual(caught.exception.receipt["stage"], "initial_export")
        self.assertEqual(caught.exception.receipt["post_entries"], 0)
        self.assertEqual(caught.exception.receipt["injected_lost_ack"], 0)
        self.assertNotIn("wrong", json.dumps(caught.exception.receipt))

    def test_exact_config_pins_loopback_split_and_duplicate_keys(self) -> None:
        check.configuration(self.path)
        for changes in (
            dict(schema=True),
            dict(extra=True),
            dict(origin="https://external.invalid"),
            dict(origin="http://127.0.0.1"),
            dict(dataset_split="heldout"),
            dict(dataset_name="../other"),
            dict(ca_sha256="a" * 64),
            dict(backend=dict(check.BACKEND, version="other")),
            dict(backend=dict(check.BACKEND, manifest_sha256="a" * 64)),
            dict(backend=dict(check.BACKEND, config_sha256="a" * 64)),
        ):
            self.write(dict(self.value, **changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check.configuration(self.path)
        self.path.write_text(json.dumps(self.value)[:-1] + ',"schema":1}')
        with self.assertRaises(ValueError):
            check.configuration(self.path)

    def test_private_paths_permissions_links_and_special_files(self) -> None:
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            check.configuration(self.path)
        self.write(self.value)
        alias = self.root / "alias"
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            check.configuration(alias)
        hard = self.root / "hard"
        os.link(self.path, hard)
        with self.assertRaises(ValueError):
            check.configuration(self.path)
        hard.unlink()
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        self.write(dict(self.value, authorization_file=str(fifo)))
        with self.assertRaises(ValueError):
            check.configuration(self.path)
        for key in ("authorization_file", "ca_file"):
            self.write(dict(self.value, **{key: "relative"}))
            with self.assertRaises(ValueError):
                check.configuration(self.path)
        self.write(self.value)
        Path(self.value["authorization_file"]).chmod(0o644)
        with self.assertRaises(ValueError):
            check.configuration(self.path)

    def test_deadline_prevents_transport_entry(self) -> None:
        remote = self.remote()
        http = check.ObservedHTTP(remote, 0)
        with self.assertRaises(TimeoutError):
            http.request(
                "GET",
                "/api/public/v2/datasets/existing_dataset",
                None,
                timeout=10,
                max_bytes=262144,
            )
        self.assertEqual((http.posts, http.gets), (0, 0))

    def test_cli_failure_is_scrubbed_and_never_creates_success_receipt(self) -> None:
        self.write(dict(self.value, origin="https://secret-sentinel.invalid"))
        output = self.root / "report.json"
        result = subprocess.run(
            [
                sys.executable,
                str(check.ROOT / "tools/langfuse_dataset_check.py"),
                str(self.path),
                str(output),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            start_new_session=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertFalse(output.exists())
        self.assertNotIn(b"secret-sentinel", result.stderr)
        self.assertNotIn(str(self.root).encode(), result.stderr)
        self.assertNotIn(b"Basic", result.stderr)
        line = result.stderr.splitlines()[0]
        diagnostic = json.loads(line.removeprefix(b"DATASET_QUALIFICATION_FAILURE "))
        self.assertEqual(diagnostic["qualification"], "failed")
        self.assertEqual(diagnostic["stage"], "configuration_or_output")
        output.write_bytes(b"retained")
        result = subprocess.run(
            [
                sys.executable,
                str(check.ROOT / "tools/langfuse_dataset_check.py"),
                str(self.path),
                str(output),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            start_new_session=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_bytes(), b"retained")

    def test_delayed_read_only_visibility_keeps_single_post(self) -> None:
        config = check.configuration(self.path)
        remote = self.remote()
        request = remote.request
        polls = 0

        def delayed(method: str, path: str, body: bytes | None, **kw: Any) -> Any:
            nonlocal polls
            if (
                method == "GET"
                and path.startswith("/api/public/dataset-items/")
                and remote.posts
            ):
                polls += 1
                remote.hidden = polls <= 2
            return request(method, path, body, **kw)

        remote.request = delayed
        with patch.object(check, "DatasetHTTP", return_value=remote):
            result = check.qualify(config)
        self.assertEqual(result["post_entries"], 1)
        self.assertEqual(result["readback_attempts"], 3)
        self.assertEqual(result["get_entries"], 8)

    def test_nonacceptance_cannot_trigger_named_lost_ack(self) -> None:
        config = check.configuration(self.path)
        remote = self.remote()
        request = remote.request

        def rejected(method: str, path: str, body: bytes | None, **kw: Any) -> Any:
            result = request(method, path, body, **kw)
            return Response(500, result.body) if method == "POST" else result

        remote.request = rejected
        with (
            patch.object(check, "DatasetHTTP", return_value=remote),
            self.assertRaises(check.QualificationFailure) as caught,
        ):
            check.qualify(config)
        self.assertEqual(caught.exception.receipt["post_entries"], 1)
        self.assertEqual(caught.exception.receipt["accepted_posts"], 0)
        self.assertEqual(caught.exception.receipt["injected_lost_ack"], 0)
        self.assertEqual(caught.exception.receipt["last_status"], 500)

    def test_programmatic_qualification_rejects_external_context_before_entry(
        self,
    ) -> None:
        config = check.configuration(self.path)
        for changes in (
            dict(origin="https://example.invalid"),
            dict(backend=dict(check.BACKEND, version="other")),
            dict(dataset_name="../unsafe"),
            dict(ca_pem="changed"),
            dict(extra=True),
        ):
            with (
                self.subTest(changes=changes),
                patch.object(check, "DatasetHTTP") as transport,
                patch.object(importlib, "import_module") as fixture,
            ):
                with self.assertRaises(ValueError):
                    check.qualify(dict(config, **changes))
                transport.assert_not_called()
                fixture.assert_not_called()
