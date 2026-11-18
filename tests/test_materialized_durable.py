"""Signed local materialization into actual durable claims, no DSPy execution."""

from __future__ import annotations

import importlib
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from refinery import evaluation as ev
from refinery.dataset_batch import Selection, capture
from refinery.dataset_materialization import prepare
from refinery.durable_dspy import Session
from refinery.materialized_use import AuthorizedBatch
from refinery.storage import VERSION


class MaterializedDurableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module(
            "test_dataset_materialization"
        ).MaterializationTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        use = replace(self.h.source.use, approve_use=lambda _: True)
        for i in range(100):
            snapshot = self.h.h.h.collect(self.h.h.selected, seed=f"owned-{i}")
            source = replace(self.h.source, snapshot=snapshot, use=use)
            if prepare(source, "synthetic").descriptor["split"] == "train":
                break
        else:
            self.fail("synthetic train split unavailable")
        self.h.source = source
        self.source = source
        self.item = self.h.add()
        self.selections = (Selection(self.item["item_id"], source),)
        manifest, _ = capture(
            self.h.registry,
            self.selections,
            purpose="optimization",
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        self.batch = AuthorizedBatch(
            manifest,
            self.h.registry,
            self.selections,
            self.h.approve,
            "explicit-test-grant",
        )

    def session(
        self, *, batches: Any = None, identity: str = "materialized-run"
    ) -> Session:
        return Session(
            self.source.store,
            VERSION,
            identity,
            self.source.snapshot,
            provider=ev.ScriptedProvider(
                (ev.Completion(b"Synthetic answer", 1, 1, True, 1),),
                clock=ev.VirtualClock(),
            ),
            limits=ev.Limits(8, 100, 100, 100),
            per_call=ev.Reservation(10, 10),
            budget_id="materialized-budget",
            retained_use=self.source.use,
            materialized_batches=(self.batch,) if batches is None else batches,
        )

    def test_enrolled_owner_can_recheck_but_generic_read_still_denies(self) -> None:
        session = self.session()
        session.claim_compile()
        session.guard("optimization")
        with self.assertRaises(ev.Halt):
            self.h.read(self.item, "optimization")
        self.assertEqual(session.provider.calls, [])
        self.assertEqual(session.row(session.store.db)[1], "compiling")

    def test_withdrawal_after_claim_denies(self) -> None:
        session = self.session()
        session.claim_compile()
        self.h.registry.withdraw(
            self.item["item_id"],
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        with self.assertRaises(ValueError):
            session.guard("optimization")
        self.assertEqual(session.provider.calls, [])

    def test_manifest_binding_prevents_legacy_or_altered_resume(self) -> None:
        session = self.session()
        session.claim_compile()
        legacy = self.session(batches=())
        with self.assertRaisesRegex(ValueError, "immutable DSPy lifecycle conflict"):
            legacy.guard("optimization")
        altered = replace(self.batch, permission_ref="another-grant")
        other = self.session(batches=(altered,))
        with self.assertRaisesRegex(ValueError, "immutable DSPy lifecycle conflict"):
            other.guard("optimization")

    def test_missing_heldout_batch_does_not_fall_back(self) -> None:
        session = self.session()
        with self.assertRaises(ev.Halt):
            session.guard("evaluation")

    def test_worker_reads_use_owned_scoped_connections(self) -> None:
        session = self.session()
        session.claim_compile()

        def work() -> None:
            with session.store.worker_context():
                session.guard("optimization")

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(work).result(timeout=20)
        session.guard("optimization")
        self.assertFalse(self.h.registry.db.in_transaction)
        self.assertFalse(self.source.use.tether_store.db.in_transaction)
        # The original connections still enforce thread affinity.
        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaises(sqlite3.ProgrammingError):
                executor.submit(self.h.registry.db.execute, "SELECT 1").result(
                    timeout=5
                )

    def test_mutated_authority_route_denies(self) -> None:
        session = self.session()
        object.__setattr__(self.batch, "approve", lambda *_: True)
        with self.assertRaises(ev.Halt):
            session.guard("optimization")
        self.assertEqual(session.provider.calls, [])

    def test_real_dispatch_receipt_and_fresh_session_recovery_use_same_binding(
        self,
    ) -> None:
        session = self.session()
        compiler = importlib.import_module("test_durable_dspy").compile_fixture
        compiled = compiler(session)
        self.assertEqual(len(session.provider.calls), 1)
        recovered = self.session()
        self.assertEqual(recovered.recover_compiled(), compiled)
        self.assertEqual(recovered.provider.calls, [])
        self.h.registry.withdraw(
            self.item["item_id"],
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        with self.assertRaises(ValueError):
            recovered.recover_compiled()
        self.assertEqual(recovered.provider.calls, [])

    def test_readonly_view_closes_handle_when_callback_raises(self) -> None:
        from refinery.readonly_view import ReadView

        view = ReadView(self.h.registry, type(self.h.registry))
        captured = None
        with self.assertRaisesRegex(RuntimeError, "synthetic callback"):
            with view.open() as scoped:
                captured = scoped.db
                raise RuntimeError("synthetic callback")
        if captured is None:
            self.fail("scoped handle not captured")
        with self.assertRaises(sqlite3.ProgrammingError):
            captured.execute("SELECT 1")
        self.assertEqual(self.h.registry.db.execute("SELECT 1").fetchone(), (1,))

    def test_replaced_database_is_not_silently_reopened(self) -> None:
        from refinery.readonly_view import ReadView

        view = ReadView(self.h.registry, type(self.h.registry))
        path = self.h.registry.path
        moved = path.with_name("original-moved.sqlite")
        path.rename(moved)
        try:
            db = sqlite3.connect(path)
            db.close()
            with self.assertRaises(ValueError):
                with view.open():
                    self.fail("replacement admitted")
        finally:
            path.unlink()
            moved.rename(path)

    def test_private_sidecar_mode_change_is_denied(self) -> None:
        import os

        session = self.session()
        path = self.h.registry.path.with_name(self.h.registry.path.name + "-wal")
        original = path.stat().st_mode & 0o777
        os.chmod(path, 0o644)
        try:
            with self.assertRaises(ValueError):
                session.guard("optimization")
        finally:
            os.chmod(path, original)
        self.assertEqual(session.provider.calls, [])

    def test_thread_affine_authority_callback_fails_closed(self) -> None:
        from refinery.feedback_use import UseDenied

        original = self.source.use.tether_store.db
        # Deliberately invalid callback: product must not suppress its affinity
        # exception or replace it with an unconditional permission grant.
        use = replace(
            self.source.use,
            approve_use=lambda _: original.execute("SELECT 1").fetchone() == (1,),
        )
        self.source = replace(self.source, use=use)
        self.selections = (Selection(self.item["item_id"], self.source),)
        self.batch = replace(self.batch, selections=self.selections)
        session = self.session()
        session.claim_compile()

        def work() -> None:
            with session.store.worker_context():
                session.guard("optimization")

        with ThreadPoolExecutor(max_workers=1) as executor:
            with self.assertRaises((sqlite3.ProgrammingError, UseDenied)):
                executor.submit(work).result(timeout=20)
        self.assertEqual(session.provider.calls, [])

    def test_sealed_cohort_cannot_be_reopened_with_materialized_train(self) -> None:
        from refinery import heldout_policy

        session = self.session()
        session.claim_compile()
        with session.store.transaction() as tx:
            heldout_policy.seal(
                session.store,
                tx,
                session.dataset,
                session.split,
                owner=session.key,
                configuration_sha256=session.config,
                candidate_sha256="a" * 64,
            )
        with self.assertRaises(ev.Halt):
            session.guard("optimization")
        self.assertEqual(session.provider.calls, [])

    def test_view_binding_mutation_denies_before_dispatch(self) -> None:
        session = self.session()
        batch = session.batches()[0]
        batch.registry.file_identity = (0, 0)
        with self.assertRaises(ev.Halt):
            session.guard("optimization")
        self.assertEqual(session.provider.calls, [])
