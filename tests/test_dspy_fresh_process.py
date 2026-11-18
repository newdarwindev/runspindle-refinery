"""Synthetic harness oracles only, never actual DSPy qualification."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import dspy_fresh_process as harness


def fixture(phase: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "phase": phase,
        "snapshot": "1" * 64,
        "candidate_sha256": "2" * 64,
        "configuration_sha256": "3" * 64,
        "binding_sha256": "4" * 64,
        "compiled_sha256": "5" * 64,
        "budget_id": harness.BUDGET,
        "heldout_count": 2,
        "new_dispatches": {"compile": 2, "evaluate": 4, "replay": 0}[phase],
        "persisted_calls": 2 if phase == "compile" else 6,
        "clock_ms": 2 if phase == "compile" else 6,
        "resume_clock_ms": None
        if phase == "compile"
        else 2
        if phase == "evaluate"
        else 6,
        "ledger_sha256": ("6" if phase == "compile" else "7") * 64,
        "report_sha256": None if phase == "compile" else "8" * 64,
        "source_sha256": {"synthetic": "9" * 64},
        "forbidden_network_attempts": 0,
        "framework_loaded": phase != "replay",
        "_pid": os.getpid() + 1000 + harness.PHASES.index(phase),
    }
    return value


class FreshProcessHarnessTests(unittest.TestCase):
    def fake_run(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        phase = argv[argv.index("--phase") + 1]
        self.directories.append(Path(argv[argv.index("--directory") + 1]))
        self.assertEqual(kwargs["timeout"], 25)
        self.assertNotIn("SYNTHETIC_SECRET", kwargs["env"])
        kwargs["stdout"].write(json.dumps(fixture(phase)).encode())
        return subprocess.CompletedProcess(argv, 0)

    def setUp(self) -> None:
        self.directories: list[Path] = []
        self.source_patch = patch.object(
            harness, "source_hashes", return_value={"synthetic": "9" * 64}
        )
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def test_three_fresh_children_and_cleanup_without_pid_in_report(self) -> None:
        with (
            patch.dict(os.environ, {"SYNTHETIC_SECRET": "do-not-inherit"}),
            patch.object(subprocess, "run", side_effect=self.fake_run) as run,
        ):
            result = harness.run()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(result["fresh_interpreters"], 3)
        self.assertEqual([p["phase"] for p in result["phases"]], list(harness.PHASES))
        self.assertTrue(all("_pid" not in p for p in result["phases"]))
        self.assertTrue(all(not p.exists() for p in self.directories))

    def test_missing_or_wrong_stage_is_not_accepted(self) -> None:
        for mutation in (None, "evaluate"):
            with self.subTest(mutation=mutation):

                def wrong(
                    argv: list[str], **kwargs: Any
                ) -> subprocess.CompletedProcess[str]:
                    value = fixture("compile")
                    value["phase"] = mutation
                    kwargs["stdout"].write(json.dumps(value).encode())
                    return subprocess.CompletedProcess(argv, 0)

                with (
                    patch.object(subprocess, "run", side_effect=wrong),
                    self.assertRaisesRegex(ValueError, "phase"),
                ):
                    harness.run()

    def test_same_process_cannot_qualify(self) -> None:
        def same(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            value = fixture("compile")
            value["_pid"] = os.getpid()
            kwargs["stdout"].write(json.dumps(value).encode())
            return subprocess.CompletedProcess(argv, 0)

        with (
            patch.object(subprocess, "run", side_effect=same),
            self.assertRaisesRegex(ValueError, "fresh process"),
        ):
            harness.run()

    def test_nonzero_timeout_invalid_json_and_cleanup(self) -> None:
        for failure in ("nonzero", "timeout", "json"):
            with self.subTest(failure=failure):

                def broken(
                    argv: list[str], **kwargs: Any
                ) -> subprocess.CompletedProcess[str]:
                    self.directories.append(Path(argv[argv.index("--directory") + 1]))
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(argv, 25)
                    kwargs["stdout"].write(b"not json")
                    return subprocess.CompletedProcess(
                        argv, 1 if failure == "nonzero" else 0
                    )

                with (
                    patch.object(subprocess, "run", side_effect=broken),
                    self.assertRaises(ValueError),
                ):
                    harness.run()
                self.assertTrue(all(not p.exists() for p in self.directories))

    def test_candidate_snapshot_config_budget_or_ledger_mutation_fails(self) -> None:
        previous = fixture("compile")
        for key in (
            "candidate_sha256",
            "snapshot",
            "configuration_sha256",
            "binding_sha256",
            "budget_id",
            "compiled_sha256",
            "source_sha256",
            "persisted_calls",
            "resume_clock_ms",
        ):
            current = fixture("evaluate")
            current[key] = 99 if key == "persisted_calls" else "changed"
            with self.subTest(key=key), self.assertRaises(ValueError):
                harness.validate_transition(previous, current)
        for key in (
            "ledger_sha256",
            "report_sha256",
            "new_dispatches",
            "framework_loaded",
        ):
            current = fixture("replay")
            current[key] = (
                1
                if key == "new_dispatches"
                else True
                if key == "framework_loaded"
                else "changed"
            )
            with self.subTest(key=key), self.assertRaises(ValueError):
                harness.validate_transition(fixture("evaluate"), current)

    def test_restored_history_is_not_counted_as_new_dispatch(self) -> None:
        current = fixture("evaluate")
        current["new_dispatches"] = 6  # Includes restored two-call prefix: invalid.
        with self.assertRaisesRegex(ValueError, "replayed compilation"):
            harness.validate_transition(fixture("compile"), current)
        current = fixture("evaluate")
        harness.validate_transition(fixture("compile"), current)

    def test_malformed_counts_digests_and_framework_receipts(self) -> None:
        for key, value in (
            ("new_dispatches", True),
            ("ledger_sha256", "invalid"),
            ("framework_loaded", 1),
            ("report_sha256", "8" * 64),
        ):
            receipt = fixture("compile")
            receipt[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                harness.validate_receipt(receipt, "compile")

    def test_missing_framework_cannot_be_reported_as_compile_success(self) -> None:
        receipt = fixture("compile")
        receipt["framework_loaded"] = False
        with self.assertRaisesRegex(ValueError, "compile outcome"):
            harness.validate_receipt(receipt, "compile")
