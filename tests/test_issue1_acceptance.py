"""Remaining issue #1 negatives against the existing synthetic lifecycle."""

import importlib
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.storage import VERSION

fixtures = importlib.import_module("test_durable")
ROOT = Path(__file__).resolve().parents[1]


class IssueOneAcceptanceTests(unittest.TestCase):
    setUp: Any = fixtures.DurableBase.setUp
    candidate: Any = fixtures.ServingTests.candidate
    adapter: Any = fixtures.ServingTests.adapter
    store: Any
    directory: Path

    def test_registry_process_death_during_write_is_atomic(self) -> None:
        registry, key, approval = self.candidate()
        before = tuple(self.store.db.iterdump())
        code = """
import os, socket, sys
from unittest.mock import patch
from refinery.storage import Store, VERSION
from refinery.serving import Registry, Approval
with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
    with Store(sys.argv[1]) as store:
        key = sys.argv[2]
        candidate = store.get("candidate", key)
        approval = Approval(candidate["candidate_sha256"],
                            candidate["evaluation_sha256"],
                            candidate["policy_sha256"], "local", "reviewer",
                            "independent-proof")
        original = store.put
        def interrupted(tx, kind, identity, value):
            original(tx, kind, identity, value)
            if kind == "approval":
                os._exit(97)
        store.put = interrupted
        Registry(store, verify_approval=lambda _: "reviewer").begin(
            VERSION, "interrupted", key, approval, expected_generation=0)
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.directory / "refinery.sqlite"), key],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 97, result.stderr)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(
            registry.state("local"), {"generation": 0, "active": None, "pending": None}
        )
        adapter = self.adapter()
        self.assertIsNone(adapter.readback("local"))
        registry.begin(VERSION, "interrupted", key, approval, expected_generation=0)
        self.assertEqual(registry.load(VERSION, "interrupted", adapter), "active")
        self.assertEqual(
            adapter.predict("local", "hello")["candidate_sha256"],
            approval.candidate_sha256,
        )
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 1
        )

    def test_changed_evaluation_and_policy_never_acquire_generation(self) -> None:
        registry, key, approval = self.candidate()
        before = tuple(self.store.db.iterdump())
        for field in ("evaluation_sha256", "policy_sha256"):
            with self.subTest(field=field):
                grant = replace(approval, **{field: "0" * 64})
                with self.assertRaisesRegex(ValueError, "approval binding conflict"):
                    registry.begin(VERSION, "forged", key, grant, expected_generation=0)
                self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_digest_shaped_invalid_candidate_and_changed_bytes_never_load(self) -> None:
        registry, key, approval = self.candidate()
        adapter = self.adapter()
        before = tuple(self.store.db.iterdump())
        original = self.store.get
        for change in ({"content": b"changed bytes"}, {"candidate_sha256": "0" * 64}):

            def changed(kind: str, identity: str) -> Any:
                value = original(kind, identity)
                return {**value, **change} if kind == "candidate" else value

            with self.subTest(change=change), patch.object(self.store, "get", changed):
                with self.assertRaisesRegex(ValueError, "candidate identity changed"):
                    registry.begin(
                        VERSION, "changed", key, approval, expected_generation=0
                    )
            self.assertEqual(tuple(self.store.db.iterdump()), before)
        with self.assertRaisesRegex(ValueError, "candidate bytes changed"):
            adapter.load(
                "changed",
                "local",
                1,
                "0" * 64,
                original("candidate", key)["content"],
                None,
            )
        self.assertIsNone(adapter.readback("local"))
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )


class LineageProofTests(unittest.TestCase):
    fixtures = importlib.import_module("test_evaluation")
    setUp: Any = fixtures.EvaluationTests.setUp
    setup_run: Any = fixtures.EvaluationTests.setup_run
    run_eval: Any = fixtures.EvaluationTests.run_eval
    data: Any
    split: Any
    provider: Any

    def test_complete_rows_without_feedback_proof_never_dispatch(self) -> None:
        from refinery.lineage import lock_split

        # Even a freshly rehashed lock and true completeness flags cannot replace
        # immutable feedback evidence. No replayable Boolean is accepted as proof.
        rows = (replace(self.data.examples[0], feedback=()), *self.data.examples[1:])
        self.data = replace(self.data, examples=rows)
        self.split = lock_split(self.data, seed="seed-2")
        self.setup_run()
        with self.assertRaisesRegex(ValueError, "invalid lineage"):
            self.run_eval()
        self.assertEqual(self.provider.calls, [])
