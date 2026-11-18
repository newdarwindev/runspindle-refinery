"""Synthetic capacity oracles and process cleanup; no actual framework execution."""

from __future__ import annotations

import contextlib
import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

from tools import offline_dspy as launcher
from tools import test_dspy_capacity as probe


def state(variant: str, final: bool = False) -> dict[str, Any]:
    known = final and variant == "known"
    return {
        "calls": 1,
        "pending": 0 if known else 1,
        "reason": "calls_exhausted",
        "tokens": int(known),
        "cost": int(known),
        "last": 0,
        "limits": {
            "calls": 1,
            "tokens": 1000,
            "cost_microunits": 1000,
            "time_ms": 1000,
        },
        "experiments": ["first"],
        "outcomes": ["settled" if known else "unknown"],
        "receipt_present": [final],
        "receipt_tokens": [1 if known else None],
        "receipt_cost": [1 if final else None],
        "ledger_sha256": "a" * 64,
        "ledger_without_reason_sha256": "b" * 64,
        "attempts_sha256": "c" * 64,
    }


def receipt(role: str, variant: str) -> dict[str, Any]:
    unresolved = role == "second" or variant == "unknown_tokens"
    return {
        "role": role,
        "variant": variant,
        "compile_entries": 1,
        "new_dispatches": int(role == "first"),
        "status": "blocked",
        "candidate_sha256": None,
        "candidate_present": False,
        "result_reason": "calls_exhausted",
        "retry_reason": "calls_exhausted",
        "network_attempts": 0,
        "train_only": True,
        "attempt_count": int(role == "first"),
        "usage": {
            "calls": 1,
            "reason": "calls_exhausted",
            "unresolved_calls": int(unresolved),
            "tokens": None if unresolved else 1,
            "cost_microunits": None if unresolved else 1,
        },
    }


class CapacityOracleTests(unittest.TestCase):
    def test_known_and_unknown_oracles(self) -> None:
        for variant in probe.VARIANTS:
            for role in ("first", "second"):
                probe.validate_worker(receipt(role, variant), role, variant)
            probe.pending(state(variant), "calls_exhausted")
            probe.validate_final(state(variant, final=True), variant)

    def test_extra_effects_or_escaped_candidate_cannot_pass(self) -> None:
        value: Any
        for variant in probe.VARIANTS:
            for role in ("first", "second"):
                for key, value in (
                    ("compile_entries", 0),
                    ("new_dispatches", 2),
                    ("status", "compiled"),
                    ("candidate_present", True),
                    ("candidate_sha256", "e" * 64),
                    ("result_reason", "arbitrary_failure"),
                    ("retry_reason", "allowed"),
                    ("network_attempts", 1),
                    ("train_only", False),
                    ("attempt_count", 2),
                ):
                    with self.subTest(variant=variant, role=role, key=key):
                        value_receipt = receipt(role, variant)
                        value_receipt[key] = value
                        with self.assertRaises(ValueError):
                            probe.validate_worker(value_receipt, role, variant)
                for key, value in (
                    ("calls", 0),
                    ("reason", None),
                    ("unresolved_calls", 3),
                    ("tokens", 99),
                    ("cost_microunits", 99),
                ):
                    with self.subTest(variant=variant, role=role, usage=key):
                        value_receipt = receipt(role, variant)
                        value_receipt["usage"][key] = value
                        with self.assertRaises(ValueError):
                            probe.validate_worker(value_receipt, role, variant)

    def test_lost_pending_charge_and_forged_settlement_rejected(self) -> None:
        for variant in probe.VARIANTS:
            for key, value in (
                ("calls", 2),
                ("pending", 0 if variant == "unknown_tokens" else 1),
                ("tokens", 99),
                ("cost", 99),
                ("reason", "unknown_or_invalid_usage"),
                ("experiments", ["second"]),
                ("receipt_present", [False]),
                ("receipt_tokens", [55]),
                ("receipt_cost", [None]),
                ("outcomes", []),
                ("last", 2),
            ):
                with self.subTest(variant=variant, key=key):
                    value_state = state(variant, final=True)
                    value_state[key] = value
                    with self.assertRaises(ValueError):
                        probe.validate_final(value_state, variant)
        stopped = state("known")
        with self.assertRaisesRegex(ValueError, "terminal cause"):
            probe.pending(stopped, None)
        stopped["pending"] = 0
        with self.assertRaisesRegex(ValueError, "reservation lost"):
            probe.pending(stopped, "calls_exhausted")

    def test_invalid_second_never_releases_first_and_all_children_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            children = [Mock(pid=10001), Mock(pid=10002)]
            for child in children:
                child.poll.return_value = None
                child.wait.return_value = 0
            held_state = state("known")
            held_state["reason"] = None
            held = {"state": held_state, "pid": 10001, "source_sha256": {}}
            denied = receipt("second", "known")
            denied["new_dispatches"] = 1
            with (
                patch.object(subprocess, "Popen", side_effect=children),
                patch.object(probe.shared, "wait_for"),
                patch.object(probe.shared, "read_json", side_effect=[held, denied]),
                patch.object(probe, "sources", return_value={}),
                patch.object(probe, "inspect_state", return_value=held_state),
            ):
                with self.assertRaisesRegex(ValueError, "provider bound"):
                    probe.orchestrate(directory, "known")
            self.assertFalse((directory / "release").exists())
            for child in children:
                child.kill.assert_called_once_with()
                self.assertEqual(child.wait.call_args.kwargs, {"timeout": 5})

    def test_cleanup_error_does_not_abandon_other_child(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            children = [Mock(pid=10001), Mock(pid=10002)]
            for child in children:
                child.poll.return_value = None
                child.wait.return_value = 0
            children[0].wait.side_effect = subprocess.TimeoutExpired("synthetic", 5)
            held_state = state("known")
            held_state["reason"] = None
            held = {"state": held_state, "pid": 10001, "source_sha256": {}}
            children[1].wait.side_effect = [1, 0]
            with (
                patch.object(subprocess, "Popen", side_effect=children),
                patch.object(probe.shared, "wait_for"),
                patch.object(probe.shared, "read_json", return_value=held),
                patch.object(probe, "sources", return_value={}),
                patch.object(probe, "inspect_state", return_value=held_state),
            ):
                with self.assertRaisesRegex(ValueError, "cleanup incomplete"):
                    probe.orchestrate(Path(temporary), "known")
            for child in children:
                child.kill.assert_called_once_with()
                self.assertEqual(child.wait.call_args.kwargs, {"timeout": 5})

    def test_failed_campaign_removes_temporary_store(self) -> None:
        storage, fixture = MagicMock(), Mock()
        fixture.setup.return_value = ("a" * 64, None, None)
        paths: list[Path] = []

        def fail(path: Path, variant: str) -> Any:
            paths.append(path)
            raise ValueError("synthetic failure")

        with (
            patch.object(
                probe.shared, "isolated", return_value=contextlib.nullcontext([])
            ),
            patch.object(importlib, "import_module", side_effect=[storage, fixture]),
            patch.object(probe, "orchestrate", side_effect=fail),
        ):
            with self.assertRaisesRegex(ValueError, "synthetic failure"):
                probe.run()
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].parent.exists())

    def test_capacity_launcher_keeps_gate_isolation_and_cleanup(self) -> None:
        replies = [
            subprocess.CompletedProcess([], 0, "{}", ""),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1),
        ]
        with (
            patch.object(launcher, "inventory", return_value={}),
            patch.object(
                launcher,
                "reviewed_closure",
                return_value=({}, {"image": launcher.IMAGE, "target": "linux/arm64"}),
            ) as closure,
            patch.object(subprocess, "run", side_effect=replies) as effect,
        ):
            self.assertEqual(launcher.execute("synthetic", "capacity"), {})
        closure.assert_called_once_with()
        argv = effect.call_args_list[0].args[0]
        self.assertEqual(argv[-1], "tools/test_dspy_capacity.py")
        self.assertEqual(effect.call_args_list[0].kwargs["timeout"], 120)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertEqual(
            effect.call_args_list[1].args[0][:3], ["docker", "rm", "--force"]
        )
        self.assertEqual(effect.call_args_list[2].args[0][:2], ["docker", "inspect"])
