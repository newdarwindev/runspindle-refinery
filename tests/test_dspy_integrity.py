"""Synthetic integrity receipt oracles, never framework qualification."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any
from unittest.mock import Mock

from tools import test_dspy_integrity as probe


def result() -> dict[str, Any]:
    return {
        "compile_entries": 1,
        "new_dispatches": 1,
        "settled_calls": 1,
        "ledger_calls": 1,
        "unresolved_calls": 0,
        "train_only": True,
        "network_attempts": 0,
        "mutated": True,
        "denial": "DSPy result identity changed",
        "state": "compiling",
        "compiled_objects": 0,
        "returned_sha256": None,
    }


def allowlist() -> dict[str, Any]:
    return {
        "compile_entries": 1,
        "candidate_present": False,
        "status": "blocked",
        "network_attempts": 0,
        "new_dispatches": 0,
        "heldout_dispatches": 0,
        "ledger_calls": 0,
    }


def locked() -> dict[str, Any]:
    return {
        "compile_entries": 1,
        "compile_dispatches": 1,
        "original_status": "compiled",
        "candidate_changed": True,
        "denial": "locked candidate digest changed",
        "new_evaluation_dispatches": 0,
        "ledger_unchanged": True,
        "attempts_unchanged": True,
        "train_only": True,
        "network_attempts": 0,
    }


class IntegrityOracleTests(unittest.TestCase):
    def test_failed_observation_is_bounded_and_still_raises(self) -> None:
        value = result()
        value.update(
            denial=None,
            candidate=b"PRIVATE_PAYLOAD",
            request="PRIVATE_REQUEST",
            returned_sha256="/private/" * 1000,
        )
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            with self.assertRaisesRegex(ValueError, "result tamper escaped"):
                probe.observed_check("synthetic_result", probe.check_result, value)
        self.assertLess(len(stream.getvalue()), 2048)
        self.assertNotIn("PRIVATE", stream.getvalue())
        self.assertNotIn("/private", stream.getvalue())
        self.assertEqual(
            json.loads(stream.getvalue())["observed"]["returned_sha256"], "redacted"
        )

    def test_protected_oracles(self) -> None:
        probe.check_result(result())
        probe.check_allowlist(allowlist())
        probe.check_locked(locked())

    def test_compiled_return_drift_is_independently_detected(self) -> None:
        value = result()
        value.update(
            denial=None, state="compiled", compiled_objects=1, returned_sha256="a" * 64
        )
        with self.assertRaisesRegex(ValueError, "^result tamper escaped$"):
            probe.check_result(value)
        probe.detected(probe.check_result, value, "result tamper escaped")

    def test_heldout_observation_not_status_detects_mutant(self) -> None:
        value = allowlist()
        value.update(new_dispatches=1, heldout_dispatches=1, ledger_calls=1)
        with self.assertRaisesRegex(ValueError, "^heldout request crossed boundary$"):
            probe.check_allowlist(value)
        probe.detected(probe.check_allowlist, value, "heldout request crossed boundary")

    def test_broken_or_wrong_failure_oracle_cannot_qualify(self) -> None:
        with self.assertRaisesRegex(ValueError, "broken guard escaped"):
            probe.detected(lambda value: None, result(), "result tamper escaped")
        value = result()
        value["compile_entries"] = 0
        with self.assertRaisesRegex(ValueError, "unrelated reason"):
            probe.detected(probe.check_result, value, "result tamper escaped")

    def test_missing_runtime_or_changed_counts_denied(self) -> None:
        for fixture, oracle, mutations in (
            (
                result,
                probe.check_result,
                (
                    ("compile_entries", 0),
                    ("new_dispatches", 2),
                    ("settled_calls", 0),
                    ("train_only", False),
                    ("network_attempts", 1),
                    ("mutated", False),
                    ("compiled_objects", 1),
                ),
            ),
            (
                allowlist,
                probe.check_allowlist,
                (
                    ("compile_entries", 0),
                    ("candidate_present", True),
                    ("status", "compiled"),
                    ("network_attempts", 1),
                ),
            ),
            (
                locked,
                probe.check_locked,
                (
                    ("compile_entries", 0),
                    ("compile_dispatches", 0),
                    ("original_status", "blocked"),
                    ("candidate_changed", False),
                    ("denial", "arbitrary_error"),
                    ("new_evaluation_dispatches", 1),
                    ("ledger_unchanged", False),
                    ("attempts_unchanged", False),
                    ("network_attempts", 1),
                    ("train_only", False),
                ),
            ),
        ):
            for key, replacement in mutations:
                with self.subTest(oracle=oracle.__name__, key=key):
                    value = fixture()
                    value[key] = replacement
                    with self.assertRaises(ValueError):
                        oracle(value)

    def test_actual_observation_wrappers_call_originals_and_restore(self) -> None:
        effects: list[str] = []

        class Provider:
            def complete(self, request: Any) -> str:
                effects.append("provider")
                return "synthetic"

        class Bootstrap:
            def compile(self, student: Any) -> str:
                effects.append("compiler")
                return "compiled-synthetic"

        ev, dspy = Mock(ScriptedProvider=Provider), Mock(BootstrapFewShot=Bootstrap)
        old_complete, old_compile = Provider.complete, Bootstrap.compile
        with probe.observations(ev, dspy) as (calls, entries):
            self.assertEqual(Provider().complete("request"), "synthetic")
            self.assertEqual(Bootstrap().compile("student"), "compiled-synthetic")
        self.assertEqual(effects, ["provider", "compiler"])
        self.assertEqual(calls, ["request"])
        self.assertEqual(entries, ["compile"])
        self.assertIs(Provider.complete, old_complete)
        self.assertIs(Bootstrap.compile, old_compile)
