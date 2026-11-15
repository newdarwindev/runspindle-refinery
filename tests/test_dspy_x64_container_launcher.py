"""Canned Engine/stream fixtures only; no Docker socket or framework use."""

from __future__ import annotations

import copy
import io
import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.test_dspy_x64_container_contract import synthetic_cleanup
from tools import dspy_x64_container_contract as policy
from tools import dspy_x64_container_launcher as launcher
from tools import dspy_x64_container_smoke as smoke


def frame(data: bytes, channel: int = 1) -> bytes:
    return bytes([channel, 0, 0, 0]) + struct.pack(">I", len(data)) + data


def fixture_result() -> dict[str, Any]:
    return {
        "status": "passed",
        "evidence_class": "actual_framework_synthetic_completion_smoke",
        "candidate_sha256": smoke.CANDIDATE_SHA256,
        "source_sha": "c" * 40,
        "actual_framework": "DSPy 3.1.0",
        "quality": "synthetic_only",
        "provider_calls": 1,
        "lm_calls": 1,
        "bootstrap": "not_run",
        "restart": "not_run",
        "live_provider": "not_run",
        "serving": "not_run",
    }


class Fake:
    def __init__(
        self,
        spec: dict[str, Any],
        *,
        extra: bytes = b"",
        empty: bool | None = False,
        deny: bool = False,
    ):
        self.spec, self.extra, self.empty, self.deny = spec, extra, empty, deny
        self.released = threading.Event()
        self.running = False
        self.signals: list[str] = []
        self.inspections = 0

    def create(self, spec: dict[str, Any]) -> str:
        if self.deny:
            raise PermissionError("synthetic create denial")
        return "d" * 64

    def inspect(self, identity: str) -> dict[str, Any]:
        self.inspections += 1
        return {
            "Id": "d" * 64,
            "Image": self.spec["Image"],
            "Mounts": [],
            "AppArmorProfile": "docker-default",
            "Config": {
                key: copy.deepcopy(value)
                for key, value in self.spec.items()
                if key not in {"Image", "HostConfig"}
            },
            "HostConfig": copy.deepcopy(self.spec["HostConfig"]),
            "State": {
                "Running": self.running,
                "Pid": 123 if self.running else 0,
                "ExitCode": 0,
                "OOMKilled": False,
                "Error": "",
            },
        }

    def start(self, identity: str) -> None:
        self.running = True

    def signal(self, identity: str, signal: str) -> None:
        self.signals.append(signal)
        if signal == "SIGUSR1":
            self.released.set()
        if signal == "SIGKILL":
            self.running = False
            self.released.set()

    def cgroup(self, pid: int, identity: str) -> str:
        return "synthetic-owned-cgroup"

    def populated(self, token: str) -> bool | None:
        return True if self.running else self.empty

    def cgroup_evidence(self, token: str) -> dict[str, Any]:
        return {**synthetic_cleanup(), "populated": self.populated(token)}

    def logs(self, identity: str) -> launcher.Stream:
        owner = self

        class Logs:
            def __init__(self) -> None:
                self.ready = io.BytesIO(
                    frame(
                        policy.encoded(
                            {"control": "ready", "contract_sha256": "b" * 64}
                        )
                        + b"\n"
                    )
                )
                self.output = io.BytesIO(
                    frame(policy.encoded(fixture_result()) + b"\n")
                    + (frame(owner.extra, 2) if owner.extra else b"")
                )

            def read(self, size: int) -> bytes:
                block = self.ready.read(size)
                if block:
                    return block
                if not owner.released.wait(1):
                    raise RuntimeError("fake release missing")
                block = self.output.read(size)
                if not block:
                    owner.running = False
                return block

            def close(self) -> None:
                pass

        return Logs()


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "new-evidence"
        self.spec = policy.create_spec("sha256:" + "a" * 64, "b" * 64, "c" * 40)

    def test_canned_success_requires_readiness_then_terminal_and_empty(self) -> None:
        fake = Fake(self.spec)
        self.assertEqual(
            launcher.launch(self.spec, fake, self.directory), fixture_result()
        )
        self.assertEqual(fake.signals, ["SIGUSR1"])
        self.assertGreaterEqual(fake.inspections, 3)
        receipt = json.loads((self.directory / "launcher-receipt.json").read_bytes())
        self.assertTrue(receipt["capture"]["drained"])
        self.assertEqual(receipt["classification"], "actual_attempt_not_qualification")

    def test_output_overrun_stops_and_preserves_all_received_bytes(self) -> None:
        overrun = b"x" * 1100000
        with self.assertRaises((ValueError, RuntimeError)):
            launcher.launch(self.spec, Fake(self.spec, extra=overrun), self.directory)
        self.assertEqual((self.directory / "stderr").read_bytes(), overrun)
        self.assertIn(overrun, (self.directory / "docker-stream.bin").read_bytes())
        receipt = json.loads((self.directory / "launcher-receipt.json").read_bytes())
        self.assertTrue(receipt["capture"]["output_stop"])

    def test_unknown_or_populated_cgroup_cannot_pass(self) -> None:
        for number, empty in enumerate((None, True)):
            directory = Path(self.temp.name) / str(number)
            with self.subTest(empty=empty), self.assertRaises(ValueError):
                launcher.launch(self.spec, Fake(self.spec, empty=empty), directory)

    def test_denied_access_has_no_start_retry_or_alternate_route(self) -> None:
        fake = Fake(self.spec, deny=True)
        with self.assertRaises(PermissionError):
            launcher.launch(self.spec, fake, self.directory)
        self.assertEqual(fake.inspections, 0)
        self.assertEqual(fake.signals, [])
        self.assertFalse(fake.running)

    def test_existing_evidence_is_never_overwritten(self) -> None:
        self.directory.mkdir()
        sentinel = self.directory / "original.log"
        sentinel.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            launcher.launch(self.spec, Fake(self.spec), self.directory)
        self.assertEqual(sentinel.read_bytes(), b"original")

    def test_wall_deadline_never_releases_framework(self) -> None:
        fake = Fake(self.spec)
        with patch(
            "tools.dspy_x64_container_launcher.time.monotonic",
            side_effect=[0.0] + [121.0] * 20,
        ):
            with self.assertRaises(RuntimeError):
                launcher.launch(self.spec, fake, self.directory)
        self.assertNotIn("SIGUSR1", fake.signals)
        self.assertEqual(fake.signals, ["SIGKILL"])

    def test_denied_log_access_uses_only_one_cleanup_kill(self) -> None:
        class DeniedLogs(Fake):
            def logs(self, identity: str) -> launcher.Stream:
                raise PermissionError("synthetic log denial")

        fake = DeniedLogs(self.spec)
        with self.assertRaises(RuntimeError):
            launcher.launch(self.spec, fake, self.directory)
        self.assertEqual(fake.signals, ["SIGKILL"])
        receipt = json.loads((self.directory / "launcher-receipt.json").read_bytes())
        self.assertEqual(receipt["capture"]["capture_error"], "PermissionError")

    def test_malformed_stream_is_retained_and_fails(self) -> None:
        self.directory.mkdir()
        result: dict[str, Any] = {"drained": False}
        data = b"broken"
        launcher.capture(
            io.BytesIO(data), self.directory, threading.Event(), result, "b" * 64
        )
        self.assertEqual((self.directory / "docker-stream.bin").read_bytes(), data)
        self.assertFalse(result["drained"])
        self.assertEqual(result["capture_error"], "RuntimeError")
