"""Synthetic receipt oracles; not actual framework qualification."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import unittest
from typing import Any
from unittest.mock import patch

from tools import offline_dspy as launcher
from tools import test_dspy_adversarial as probe


def fixture(name: str) -> dict[str, Any]:
    calls, unresolved, reason = probe.CASES[name]
    return {
        "case": name,
        "compile_entries": 1,
        "status": "blocked",
        "reason": reason,
        "candidate": None,
        "candidate_sha256": None,
        "new_dispatches": calls,
        "ledger": {
            "calls": 1,
            "unresolved_calls": unresolved,
            "reason": reason,
            "tokens": None if unresolved else 1,
            "cost_microunits": None if unresolved else 1,
        },
        "retry_reason": reason,
        "attempt_outcomes": ["unknown" if unresolved else "settled"],
        "train_only": True,
        "network_attempts": 0,
    }


class AdversarialOracleTests(unittest.TestCase):
    def test_failed_observation_still_raises_without_success_output(self) -> None:
        receipt = fixture("provider_failure")
        receipt["reason"] = "unexpected_synthetic_reason"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaisesRegex(ValueError, "unexpected failure"):
                probe.validate_observed(receipt)
        self.assertEqual(out.getvalue(), "")
        value = json.loads(err.getvalue())
        self.assertEqual(value["case"], "provider_failure")
        self.assertEqual(value["reason"], "unexpected_synthetic_reason")
        self.assertEqual(value["ledger"]["reason"], "provider_unknown")
        self.assertLess(len(err.getvalue()), 2048)

    def test_diagnostic_cannot_dump_payload_or_unbounded_values(self) -> None:
        receipt = fixture("cancel")
        receipt.update(
            reason="secret /private/path " * 10000,
            candidate=b"PRIVATE_PAYLOAD",
            request="PRIVATE_REQUEST",
        )
        receipt["ledger"].update(reason="line\nsecret", tokens=10**1000)
        encoded = json.dumps(probe.diagnostic(receipt))
        self.assertLess(len(encoded), 2048)
        for private in ("PRIVATE", "secret", "/private", "request"):
            self.assertNotIn(private, encoded)
        self.assertEqual(probe.diagnostic(receipt)["reason"], "redacted")

    def test_passing_observation_emits_no_diagnostic(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            probe.validate_observed(fixture("cancel"))
        self.assertEqual(err.getvalue(), "")

    def test_five_expected_receipts(self) -> None:
        self.assertEqual(len(probe.CASES), 5)
        for name in probe.CASES:
            probe.validate(fixture(name))
            wrapped = fixture(name)
            wrapped["reason"] = "optimizer_failure"
            probe.validate(wrapped)

    def test_extra_dispatch_and_lost_reservation_mutations(self) -> None:
        for name in probe.CASES:
            for key, value in (
                ("new_dispatches", 2),
                ("compile_entries", 0),
                ("candidate", b"escaped"),
                ("candidate_sha256", "a" * 64),
                ("status", "compiled"),
                ("reason", "arbitrary"),
                ("train_only", False),
                ("network_attempts", 1),
                ("retry_reason", "allowed"),
                ("attempt_outcomes", []),
            ):
                with self.subTest(name=name, key=key):
                    receipt = fixture(name)
                    receipt[key] = value
                    with self.assertRaises(ValueError):
                        probe.validate(receipt)
            for key, value in (
                ("calls", 0),
                ("unresolved_calls", 1 if name == "exhaustion" else 0),
                ("reason", "optimizer_failure"),
                ("tokens", 0),
                ("cost_microunits", 0),
            ):
                with self.subTest(name=name, ledger=key):
                    receipt = fixture(name)
                    receipt["ledger"][key] = value
                    with self.assertRaises(ValueError):
                        probe.validate(receipt)

    def test_real_adoption_gate_used_before_adversarial_launch(self) -> None:
        with (
            patch.object(launcher, "inventory", return_value={}),
            patch.object(
                launcher, "reviewed_closure", side_effect=ValueError("closure denied")
            ) as gate,
            patch.object(subprocess, "run") as effect,
        ):
            with self.assertRaisesRegex(ValueError, "closure denied"):
                launcher.execute("synthetic", "adversarial")
            gate.assert_called_once_with()
            effect.assert_not_called()

    def test_adversarial_launch_retains_isolation_and_cleanup(self) -> None:
        declared: dict[str, Any] = {"synthetic": True}
        replies = [
            subprocess.CompletedProcess([], 0, '{"synthetic":true}', ""),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1),
        ]
        with (
            patch.object(launcher, "inventory", return_value=copy.deepcopy(declared)),
            patch.object(
                launcher,
                "reviewed_closure",
                return_value=(
                    declared,
                    {"image": launcher.IMAGE, "target": "linux/arm64"},
                ),
            ),
            patch.object(subprocess, "run", side_effect=replies) as effect,
        ):
            self.assertEqual(
                launcher.execute("synthetic", "adversarial"), {"synthetic": True}
            )
        call = effect.call_args_list[0]
        argv = call.args[0]
        self.assertEqual(argv[-1], "tools/test_dspy_adversarial.py")
        self.assertEqual(call.kwargs["timeout"], 120)
        for flag, value in (
            ("--network", "none"),
            ("--platform", "linux/arm64"),
            ("--pull", "never"),
            ("--cap-drop", "ALL"),
            ("--security-opt", "no-new-privileges"),
        ):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn("--read-only", argv)
        self.assertEqual(
            effect.call_args_list[1].args[0][:3], ["docker", "rm", "--force"]
        )
        self.assertEqual(effect.call_args_list[2].args[0][:2], ["docker", "inspect"])
