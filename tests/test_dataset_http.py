"""Dataset-only profile: synthetic loopback TLS, not an actual Langfuse service."""

import base64
import copy
import importlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import dataset_contract as c
from refinery import langfuse_http as transport
from refinery.langfuse_http import DatasetHTTP, LangfuseHTTP, validate_dataset_request


def body() -> bytes:
    descriptor = c.binding(
        "local", "a" * 64, {"prompt": "Привет"}, b"answer", "b" * 64, "train"
    )
    return c.canonical(
        dict(
            id="c" * 64,
            datasetName="existing_dataset",
            input={"prompt": "Привет"},
            expectedOutput="answer",
            metadata=dict(runspindle_dataset=descriptor, projection_sha256="c" * 64),
            status="ACTIVE",
        )
    )


class DatasetHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module("test_langfuse_http").HTTPTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.http = DatasetHTTP(
            self.h.origin, self.h.http.authorization, self.h.http.ca_pem
        )

    def request(self, **changes: Any) -> Any:
        args: dict[str, Any] = dict(
            method="POST",
            path="/api/public/dataset-items",
            body=body(),
            timeout=3,
            max_bytes=262144,
        )
        args.update(changes)
        return self.http.request(**args)

    def test_actual_tls_dataset_profile_no_proxy_no_auth_repr(self) -> None:
        with patch.dict(
            "os.environ",
            HTTPS_PROXY="http://127.0.0.1:1",
            LANGFUSE_SECRET_KEY="unused-secret",
        ):
            self.assertEqual(self.request().status, 200)
        self.assertEqual(
            self.h.seen,
            [("/api/public/dataset-items", self.http.authorization, body())],
        )
        for path in (
            "/api/public/v2/datasets/existing_dataset",
            "/api/public/dataset-items/" + "c" * 64,
        ):
            self.assertEqual(
                self.request(method="GET", path=path, body=None).status, 200
            )
        self.assertNotIn(self.http.authorization, repr(self.http))

    def test_cross_profile_and_malformed_paths_reject_before_child(self) -> None:
        bad = [
            "/api/public/scores",
            "/api/public/v2/datasets/../x",
            "/api/public/v2/datasets/%2e%2e",
            "/api/public/v2/datasets/name?x=y",
            "/api/public/v2/datasets/name#x",
            "https://elsewhere.invalid/api/public/dataset-items",
            "//elsewhere.invalid/x",
            "/api/public/dataset-items/" + "A" * 64,
            "/api/public/datasets",
            "/api/public/v2/datasets/name/",
            "/api/public/v2/datasets/name\\x",
        ]
        with patch.object(subprocess, "run") as run:
            for path in bad:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.request(method="GET", path=path, body=None)
            for method in ("DELETE", "PATCH", "PUT", "HEAD", "post"):
                with self.subTest(method=method), self.assertRaises(ValueError):
                    self.request(method=method)
            with self.assertRaises(ValueError):
                self.h.http.request(
                    "POST",
                    "/api/public/dataset-items",
                    body(),
                    timeout=3,
                    max_bytes=65536,
                )
            run.assert_not_called()

    def test_exact_body_schema_content_and_active_only(self) -> None:
        valid = json.loads(body())
        mutations = [
            dict(status="ARCHIVED"),
            dict(sourceTraceId="trace"),
            dict(sourceObservationId="observation"),
            dict(mediaReferences=[]),
            dict(expectedOutput="changed"),
            dict(input={"prompt": "changed"}),
            dict(id=True),
            dict(datasetName="a/b"),
        ]
        for change in mutations:
            candidate = copy.deepcopy(valid)
            candidate.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_dataset_request(
                    "POST", "/api/public/dataset-items", c.canonical(candidate)
                )
        for raw in (body() + b" ", b'{"id":1,"id":2}', b'{"input":NaN}', b"x" * 262145):
            with self.assertRaises(ValueError):
                validate_dataset_request("POST", "/api/public/dataset-items", raw)
        for output in ("bad\n", "@@@langfuseMedia:secret"):
            candidate = copy.deepcopy(valid)
            candidate["expectedOutput"] = output
            with self.assertRaises(ValueError):
                validate_dataset_request(
                    "POST", "/api/public/dataset-items", c.canonical(candidate)
                )
        validate_dataset_request("POST", "/api/public/dataset-items", body())

    def test_transport_profile_payload_and_scrubbed_error(self) -> None:
        with patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("secret", 1),
        ) as run:
            with self.assertRaisesRegex(
                TimeoutError, "^bounded Langfuse request unconfirmed$"
            ):
                self.request(timeout=1)
        args, kwargs = run.call_args
        self.assertNotIn(self.http.authorization, str(args))
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["profile"], "dataset-v1")
        self.assertEqual(payload["authorization"], self.http.authorization)
        self.assertEqual(set(kwargs["env"]), {"PATH", "LANG"})
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(args[0][1:3], ["-I", "-B"])

    def test_redirect_deadline_and_returned_bound(self) -> None:
        self.h.mode = "redirect"
        self.assertEqual(self.request().status, 302)
        self.assertEqual(len(self.h.seen), 1)
        self.h.mode = "large"
        self.assertEqual(len(self.request().body), 65537)
        with self.assertRaises(ValueError):
            self.request(max_bytes=65536)
        self.h.mode = "slow"
        processes = []
        original_popen = subprocess.Popen

        def observed_popen(*args: Any, **kwargs: Any) -> Any:
            child = original_popen(*args, **kwargs)
            processes.append(child)
            return child

        with patch.object(subprocess, "Popen", side_effect=observed_popen):
            with self.assertRaises(TimeoutError):
                self.request(timeout=1)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertEqual(len(self.h.seen), 4)

    def test_worker_profiles_and_bounds_fail_closed_without_connection(self) -> None:
        payload = dict(
            profile="dataset-v1",
            origin=self.h.origin,
            authorization=self.http.authorization,
            ca_pem=self.http.ca_pem,
            method="GET",
            path="/api/public/scores",
            body=None,
            timeout=3,
            max_bytes=262144,
        )
        cases: list[dict[str, Any]] = [
            dict(profile="unknown"),
            dict(profile=None),
            dict(timeout=True),
            dict(max_bytes=262145),
            dict(profile="score-v1", path="/api/public/dataset-items/" + "c" * 64),
            dict(
                method="POST",
                path="/api/public/dataset-items",
                body=base64.b64encode(b'{"status":"ARCHIVED"}').decode(),
            ),
        ]
        for changes in cases:
            value = dict(payload, **changes)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(transport.__file__).resolve()),
                    "--worker",
                ],
                input=json.dumps(value).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                env={"LANG": "C.UTF-8"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
        self.assertEqual(self.h.seen, [])

        class Unknown(LangfuseHTTP):
            pass

        with self.assertRaises(ValueError):
            Unknown(self.h.origin, self.http.authorization).request(
                "POST", "/api/public/scores", b"{}", timeout=1, max_bytes=1
            )

    def test_child_rejects_duplicate_nonfinite_nested_and_descriptor_mutations(
        self,
    ) -> None:
        valid = json.loads(body())
        malformed = [b'{"id":1,"id":2}', b'{"input":NaN}', b"[" * 40 + b"0" + b"]" * 40]
        for field, changed in (
            ("version", "wrong"),
            ("split", "other"),
            ("item_id", "d" * 64),
            ("source_identity_sha256", "e" * 64),
        ):
            value = copy.deepcopy(valid)
            value["metadata"]["runspindle_dataset"][field] = changed
            malformed.append(c.canonical(value))
        extra = copy.deepcopy(valid)
        extra["unapproved"] = True
        malformed.append(c.canonical(extra))
        payload: dict[str, Any] = dict(
            profile="dataset-v1",
            origin=self.h.origin,
            authorization=self.http.authorization,
            ca_pem=self.http.ca_pem,
            method="POST",
            path="/api/public/dataset-items",
            body=None,
            timeout=3,
            max_bytes=262144,
        )
        envelopes = []
        for raw in malformed:
            payload["body"] = base64.b64encode(raw).decode()
            envelopes.append(json.dumps(payload).encode())
        good = dict(payload, body=base64.b64encode(body()).decode())
        envelopes.append(json.dumps(good).encode()[:-1] + b',"profile":"dataset-v1"}')
        envelopes.append(json.dumps(dict(good, extra=True)).encode())
        missing = dict(good)
        del missing["profile"]
        envelopes.append(json.dumps(missing).encode())
        for envelope in envelopes:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(transport.__file__).resolve()),
                    "--worker",
                ],
                input=envelope,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                env={"LANG": "C.UTF-8"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.stdout, result.stderr), (b"", b""))
        self.assertEqual(self.h.seen, [])
