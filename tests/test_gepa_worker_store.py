"""Real SQLite/thread ownership tests; not actual GEPA framework qualification."""

from __future__ import annotations

import importlib
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery import gepa_codec as codec
from refinery import gepa_optimizer as gepa
from refinery.dspy_optimizer import case
from refinery.durable_evaluation import SQLiteBudget

storage: Any = importlib.import_module("refinery.storage")
fixture: Any = importlib.import_module("test_durable")


class WorkerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = storage.Store(Path(temp.name) / "store.sqlite")
        self.addCleanup(self.store.close)
        self.snapshot, _, _ = fixture.setup(self.store)
        self.dataset, self.split = self.store.lineage(self.snapshot)
        self.example = next(
            e
            for e in self.dataset.examples
            if self.split.partition(e.source.family_id) == "train"
        )
        self.provider: Any = ev.ScriptedProvider(
            (ev.Completion(self.example.gold, 1, 1, True, 0),), clock=ev.VirtualClock()
        )
        self.budget: Any = SQLiteBudget(
            self.store,
            "shared",
            ev.Limits(16, 100, 100, 100),
            clock_ms=self.provider.clock,
            experiment="gepa",
        )
        self.dispatch = gepa.Transport(
            self.example,
            self.split,
            config="a" * 64,
            provider=self.provider,
            budget=self.budget,
            per_call=ev.Reservation(10, 10),
            check_use=lambda: self.store.get("lineage", self.snapshot),
        )

    def worker(self, function: Any) -> Any:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(function).result(timeout=10)

    def invoke(self) -> Any:
        with self.dispatch.execution_scope():
            return self.dispatch.invoke_sealed(
                lambda **_: self.dispatch.forward([]),
                "optimization",
                codec.INITIAL,
                case(self.example),
            )

    def test_worker_dispatch_and_metric_preserve_affinity_and_accounting(self) -> None:
        original = sqlite3.connect
        created: list[tuple[int, Any, dict[str, Any]]] = []

        def connect(*args: Any, **kwargs: Any) -> Any:
            connection = original(*args, **kwargs)
            if str(args[0]) != ":memory:":
                created.append((threading.get_ident(), connection, kwargs))
            return connection

        owner = threading.get_ident()
        with patch.object(storage.sqlite3, "connect", side_effect=connect):
            self.worker(self.invoke)
        self.assertEqual(len(created), 1)
        self.assertNotEqual(created[0][0], owner)
        self.assertNotEqual(created[0][2].get("check_same_thread"), False)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.budget.snapshot()["calls"], 1)
        self.assertEqual(self.budget.snapshot()["unresolved_calls"], 0)

        def metric() -> Any:
            with self.dispatch.execution_scope():
                return self.dispatch.metric_feedback(
                    case(self.example),
                    self.example.gold.decode(),
                    self.example.gold.decode(),
                    None,
                )

        self.assertEqual(self.worker(metric), (1.0, "correct"))

    def test_foreign_access_without_scope_is_denied(self) -> None:
        with self.assertRaisesRegex(ValueError, "context required"):
            self.worker(lambda: self.store.db.execute("SELECT 1"))
        self.assertEqual(self.provider.calls, [])

    def test_nested_scope_reuses_connection_and_closes_on_worker(self) -> None:
        def work() -> None:
            with self.store.worker_context() as first:
                with self.store.worker_context() as second:
                    self.assertIs(first, second)
                    with self.store.transaction():
                        self.assertIsInstance(
                            self.store.get("lineage", self.snapshot), dict
                        )
            with self.assertRaises(sqlite3.ProgrammingError):
                first.execute("SELECT 1")
            with self.assertRaisesRegex(ValueError, "context required"):
                _ = self.store.db
            with self.store.worker_context() as fresh:
                self.assertIsNot(first, fresh)

        self.worker(work)

    def test_owner_transaction_cannot_be_joined_by_worker(self) -> None:
        with self.store.transaction() as tx:
            tx.execute(
                "INSERT INTO experiments VALUES('owner',?,'open',NULL)",
                (storage.pack("binding"),),
            )
            with self.assertRaisesRegex(ValueError, "admission unavailable"):
                self.worker(lambda: self.enter_worker())
            self.assertTrue(self.store.db.in_transaction)
        self.assertEqual(
            self.store.db.execute(
                "SELECT state FROM experiments WHERE identity='owner'"
            ).fetchone()[0],
            "open",
        )

    def enter_worker(self) -> None:
        with self.store.worker_context():
            pass

    def test_owner_close_and_second_worker_rejected_while_scope_active(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def hold() -> None:
            with self.store.worker_context():
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test release timeout")

        with ThreadPoolExecutor(max_workers=2) as executor:
            held = executor.submit(hold)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaisesRegex(ValueError, "worker is active"):
                    self.store.close()
                with self.assertRaisesRegex(ValueError, "worker is active"):
                    _ = self.store.db
                with self.assertRaisesRegex(ValueError, "admission unavailable"):
                    executor.submit(self.enter_worker).result(timeout=5)
            finally:
                release.set()
                held.result(timeout=5)
        self.assertEqual(self.store.db.execute("SELECT 1").fetchone()[0], 1)

    def test_transaction_failure_rolls_back_and_preserves_outer_owner(self) -> None:
        def work() -> None:
            with self.store.worker_context():
                with self.assertRaisesRegex(RuntimeError, "synthetic"):
                    with self.store.transaction() as tx:
                        tx.execute(
                            "INSERT INTO experiments VALUES('failed',?,'open',NULL)",
                            (storage.pack("binding"),),
                        )
                        raise RuntimeError("synthetic")
                self.assertFalse(self.store.db.in_transaction)
                with self.store.transaction() as tx:
                    with self.assertRaises(sqlite3.OperationalError):
                        with self.store.transaction():
                            self.fail("nested transaction admitted")
                    self.assertTrue(tx.in_transaction)

        self.worker(work)
        self.assertIsNone(
            self.store.db.execute(
                "SELECT state FROM experiments WHERE identity='failed'"
            ).fetchone()
        )

    def test_unfinished_transaction_is_rolled_back_not_committed(self) -> None:
        def work() -> None:
            with self.store.worker_context() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO experiments VALUES('unfinished',?,'open',NULL)",
                    (storage.pack("binding"),),
                )

        with self.assertRaisesRegex(ValueError, "unfinished transaction"):
            self.worker(work)
        self.assertIsNone(
            self.store.db.execute(
                "SELECT state FROM experiments WHERE identity='unfinished'"
            ).fetchone()
        )
        self.worker(self.enter_worker)

    def test_post_reservation_failure_retains_charge_and_cleans_context(self) -> None:
        def interrupt(stage: str) -> None:
            if stage == "after_budget_reservation":
                raise RuntimeError("interrupted")

        self.budget.checkpoint = interrupt
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.worker(self.invoke)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["unresolved_calls"], 1)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1
        )
        self.worker(self.enter_worker)

    def test_unknown_provider_preserves_unresolved_charge(self) -> None:
        self.provider.script = ()
        # New transport binds the deliberately empty immutable script.
        self.dispatch = gepa.Transport(
            self.example,
            self.split,
            config="a" * 64,
            provider=self.provider,
            budget=self.budget,
            per_call=ev.Reservation(10, 10),
            check_use=lambda: None,
        )
        with self.assertRaisesRegex(ev.Halt, "provider_unknown"):
            self.worker(self.invoke)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.budget.snapshot()["unresolved_calls"], 1)

    def test_overlap_poison_rejects_both_invocations_without_effects(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def hold() -> None:
            with self.dispatch.execution_scope():
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test release timeout")

        with ThreadPoolExecutor(max_workers=1) as executor:
            held = executor.submit(hold)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaisesRegex(ev.Halt, "gepa_concurrent_execution"):
                    with self.dispatch.execution_scope():
                        self.fail("overlap admitted")
            finally:
                release.set()
            with self.assertRaisesRegex(ev.Halt, "gepa_concurrent_execution"):
                held.result(timeout=5)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)
        self.assertEqual(self.budget.snapshot()["reason"], "gepa_concurrent_execution")

    def test_cleanup_attempts_close_after_rollback_failure_and_poisons_store(
        self,
    ) -> None:
        events: list[str] = []
        original = sqlite3.connect

        class FailingRollback(sqlite3.Connection):
            def rollback(self) -> None:
                events.append("rollback")
                super().rollback()
                raise RuntimeError("synthetic rollback failure")

            def close(self) -> None:
                events.append("close")
                super().close()

        def connect(*args: Any, **kwargs: Any) -> Any:
            if str(args[0]) != ":memory:":
                kwargs["factory"] = FailingRollback
            return original(*args, **kwargs)

        def work() -> None:
            with self.store.worker_context() as db:
                db.execute("BEGIN IMMEDIATE")

        with patch.object(storage.sqlite3, "connect", side_effect=connect):
            with self.assertRaisesRegex(RuntimeError, "rollback failure"):
                self.worker(work)
        self.assertEqual(events, ["rollback", "close"])
        with self.assertRaisesRegex(ValueError, "cleanup failed"):
            _ = self.store.db
        with self.assertRaisesRegex(ValueError, "admission unavailable"):
            self.worker(self.enter_worker)

    def test_worker_schema_validation_failure_cleans_scope(self) -> None:
        self.store.db.execute("PRAGMA user_version=9")
        try:
            with self.assertRaisesRegex(ValueError, "incompatible refinery store"):
                self.worker(self.enter_worker)
        finally:
            self.store.db.execute("PRAGMA user_version=1")
        self.worker(self.enter_worker)

    def test_cancelled_budget_does_not_dispatch_on_worker(self) -> None:
        self.budget.stop("cancelled")
        with self.assertRaisesRegex(ev.Halt, "cancelled"):
            self.worker(self.invoke)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_authority_revocation_after_reservation_is_observed_in_worker(self) -> None:
        state = {"revoked": False, "checks": 0}

        def check() -> None:
            self.store.get("lineage", self.snapshot)
            self.store.db.execute("SELECT state FROM experiments").fetchall()
            if state["revoked"]:
                state["checks"] += 1
                raise ev.Halt("authority_revoked")

        def checkpoint(stage: str) -> None:
            if stage == "after_budget_reservation":
                state["revoked"] = True

        self.dispatch.check_use = check
        self.budget.checkpoint = checkpoint
        with self.assertRaisesRegex(ev.Halt, "authority_revoked"):
            self.worker(self.invoke)
        self.assertEqual(state["checks"], 1)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["unresolved_calls"], 1)
        self.assertEqual(self.budget.snapshot()["reason"], "authority_revoked")

    def test_worker_authority_callback_binding_mutation_prevents_reservation(
        self,
    ) -> None:
        def change() -> None:
            self.store.db.execute("SELECT 1").fetchone()
            self.provider.clock = ev.VirtualClock()

        self.dispatch.check_use = change
        with self.assertRaisesRegex(ev.Halt, "dispatch_binding_changed"):
            self.worker(self.invoke)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_reservation_write_failure_rolls_back_before_provider(self) -> None:
        original = storage.Store.room

        def deny(tx: Any, table: Any, limit: Any = storage.MAX_OBJECTS) -> Any:
            if table == "attempts":
                raise ValueError("synthetic reservation write failure")
            return original(tx, table, limit)

        before = tuple(self.store.db.execute("SELECT * FROM budgets"))
        with patch.object(storage.Store, "room", side_effect=deny):
            with self.assertRaisesRegex(ValueError, "reservation write failure"):
                self.worker(self.invoke)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(tuple(self.store.db.execute("SELECT * FROM budgets")), before)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0
        )

    def test_nested_execution_scope_does_not_bypass_dispatch_reentrancy(self) -> None:
        def work() -> None:
            with self.dispatch.execution_scope():
                with self.dispatch.execution_scope():
                    self.dispatch.invoke_sealed(
                        lambda **_: self.dispatch.invoke_sealed(
                            lambda **_: self.dispatch.forward([]),
                            "optimization",
                            codec.INITIAL,
                            case(self.example),
                        ),
                        "optimization",
                        codec.INITIAL,
                        case(self.example),
                    )

        with self.assertRaisesRegex(ev.Halt, "reentrant_dispatch"):
            self.worker(work)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_memory_store_is_not_silently_reopened_as_another_database(self) -> None:
        with storage.Store(":memory:") as store:

            def work() -> None:
                with store.worker_context():
                    self.fail("memory Store reopened")

            with self.assertRaisesRegex(ValueError, "admission unavailable"):
                self.worker(work)
