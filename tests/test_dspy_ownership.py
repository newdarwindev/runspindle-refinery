"""Synthetic ownership receipt/cleanup tests, never actual DSPy evidence."""

from __future__ import annotations

import importlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from tools import test_dspy_ownership as probe


def fixture(outcome: str) -> dict[str, Any]:
    denied = outcome == "denied"
    return {
        "outcome": outcome,
        "initial_rejection": "interrupted_or_owned_dspy_phase" if denied else None,
        "retry_rejection": "interrupted_or_owned_dspy_phase" if denied else None,
        "experiment_count": 1,
        "compiled_objects": 0 if denied else 1,
        "stored_state": "compiling" if denied else "compiled",
        "stored_report": None if denied else "2" * 64,
        "attempt_outcomes": ["unknown"] if denied else ["settled", "settled"],
        "network_attempts": 0,
        "train_only": True,
        "compile_entries": int(outcome == "compiled"),
        "new_dispatches": 2 if outcome == "compiled" else 0,
        "ledger": {
            "calls": 1 if denied else 2,
            "unresolved_calls": int(denied),
            "reason": None,
            "tokens": None if denied else 2,
            "cost_microunits": None if denied else 2,
        },
        "candidate_sha256": None if denied else "a" * 64,
        "compiled_status": None if denied else "compiled",
        "snapshot": "b" * 64,
        "binding_sha256": "c" * 64,
        "configuration_sha256": "d" * 64,
        "source_sha256": {"synthetic.py": "e" * 64},
        "ledger_sha256": "f" * 64,
        "attempts_sha256": "1" * 64,
        "pid": os.getpid() + 1000 + ("denied", "compiled", "recovered").index(outcome),
    }


class OwnershipOracleTests(unittest.TestCase):
    def values(self) -> list[dict[str, Any]]:
        return [fixture(outcome) for outcome in ("denied", "compiled", "recovered")]

    def test_nominal_receipts_normalized(self) -> None:
        result = probe.validate_results(self.values())
        self.assertEqual(len(result), 3)
        self.assertTrue(all("pid" not in value for value in result))

    def test_only_source_justified_initial_rejections_are_accepted(self) -> None:
        receipt = fixture("denied")
        receipt["initial_rejection"] = "lifecycle_claim_changed"
        probe.validate_case(receipt, "denied")
        receipt["retry_rejection"] = "lifecycle_claim_changed"
        with self.assertRaisesRegex(ValueError, "pending ownership"):
            probe.validate_case(receipt, "denied")
        receipt = fixture("denied")
        receipt["initial_rejection"] = "arbitrary_failure"
        with self.assertRaisesRegex(ValueError, "initial ownership"):
            probe.validate_case(receipt, "denied")

    def test_mutated_effects_cannot_pass(self) -> None:
        for outcome in ("denied", "compiled", "recovered"):
            for key, value in (
                ("experiment_count", 2),
                ("compiled_objects", 3),
                ("stored_state", "complete"),
                ("attempt_outcomes", []),
                ("new_dispatches", 3),
                ("compile_entries", 2),
                ("network_attempts", 1),
                ("train_only", False),
                ("compiled_status", "escaped"),
                ("candidate_sha256", "invalid"),
            ):
                with self.subTest(outcome=outcome, key=key):
                    receipt = fixture(outcome)
                    receipt[key] = value
                    with self.assertRaises(ValueError):
                        probe.validate_case(receipt, outcome)
            for key, value in (
                ("calls", 0),
                ("unresolved_calls", 0 if outcome == "denied" else 1),
                ("tokens", 0),
                ("cost_microunits", 0),
                ("reason", "reset"),
            ):
                with self.subTest(outcome=outcome, ledger=key):
                    receipt = fixture(outcome)
                    receipt["ledger"][key] = value
                    with self.assertRaises(ValueError):
                        probe.validate_case(receipt, outcome)

    def test_changed_binding_recovery_and_same_process_rejected(self) -> None:
        for key in (
            "snapshot",
            "configuration_sha256",
            "binding_sha256",
            "source_sha256",
            "candidate_sha256",
            "ledger_sha256",
            "attempts_sha256",
        ):
            values = self.values()
            values[-1][key] = "9" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.validate_results(values)
        for pid in (os.getpid(), self.values()[0]["pid"], None):
            values = self.values()
            values[-1]["pid"] = pid
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                probe.validate_results(values)
        with self.assertRaises(ValueError):
            probe.validate_results(self.values()[:2])

    def test_failure_kills_and_reaps_only_started_children(self) -> None:
        for fail_second_launch in (False, True):
            with (
                self.subTest(launch=fail_second_launch),
                tempfile.TemporaryDirectory() as temporary,
            ):
                children = [Mock(), Mock()]
                for child in children:
                    child.poll.return_value = None
                    child.wait.return_value = 0
                effects = (
                    [children[0], OSError("synthetic launch failure")]
                    if fail_second_launch
                    else children
                )
                with (
                    patch.object(subprocess, "Popen", side_effect=effects),
                    patch.object(
                        probe,
                        "wait_for",
                        side_effect=ValueError("synthetic barrier failure"),
                    ),
                ):
                    with self.assertRaises((OSError, ValueError)):
                        probe.orchestrate(Path(temporary))
                for child in children[: 1 if fail_second_launch else 2]:
                    child.kill.assert_called_once_with()
                    child.wait.assert_called_once_with(timeout=5)
                if fail_second_launch:
                    children[1].kill.assert_not_called()

    def test_invalid_loser_never_releases_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            receipt = fixture("denied")
            receipt["new_dispatches"] = 1
            children = [Mock(), Mock()]
            for child in children:
                child.poll.return_value = None
                child.wait.return_value = 0
            with (
                patch.object(subprocess, "Popen", side_effect=children),
                patch.object(probe, "wait_for"),
                patch.object(probe, "read_json", return_value=receipt),
            ):
                with self.assertRaisesRegex(ValueError, "effects or charges"):
                    probe.orchestrate(directory)
            self.assertFalse((directory / "release").exists())
            for child in children:
                child.kill.assert_called_once_with()
                child.wait.assert_called_once_with(timeout=5)

    def test_barrier_timeout_and_oversized_receipt_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "barrier timeout"):
            probe.wait_for(lambda: False, seconds=0)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            path.write_bytes(b"x" * (1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "receipt too large"):
                probe.read_json(path)

    def test_failed_orchestration_removes_temporary_store(self) -> None:
        paths: list[Path] = []
        storage = MagicMock()
        fixture_module = Mock()
        fixture_module.setup.return_value = ("a" * 64, None, None)

        def fail(directory: Path) -> Any:
            paths.append(directory)
            raise ValueError("synthetic failure")

        import contextlib

        with (
            patch.object(probe, "isolated", return_value=contextlib.nullcontext([])),
            patch.object(
                importlib, "import_module", side_effect=[storage, fixture_module]
            ),
            patch.object(probe, "orchestrate", side_effect=fail),
        ):
            with self.assertRaisesRegex(ValueError, "synthetic failure"):
                probe.run()
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())
