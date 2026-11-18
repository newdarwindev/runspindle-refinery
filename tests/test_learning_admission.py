"""Actual signed local admission/lifecycle tests; no framework execution."""

from __future__ import annotations

import importlib
import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery.durable_dspy import Session
from refinery.optimization import validate_dataset
from refinery.storage import VERSION
from tools.test_gepa_heldout_materialized import persisted


class LearningAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module(
            "test_materialized_durable"
        ).MaterializedDurableTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.batches = self.h.mixed_batches()
        self.source = self.h.source
        train, self.heldout = validate_dataset(
            *self.source.store.lineage(self.source.snapshot)
        )
        self.train = train[0]
        self.script = (
            ev.Completion(b"wrong", 1, 1, True, 0),
            ev.Completion(b"Read carefully and answer exactly.", 1, 1, True, 0),
            ev.Completion(self.train.gold, 1, 1, True, 0),
        ) + tuple(
            ev.Completion(e.gold, 1, 1, True, 0) for _ in range(2) for e in self.heldout
        )

    def admit(self, intent: Any = "start", **changes: Any) -> Session:
        options: Any = dict(
            intent=intent,
            provider=ev.ScriptedProvider(self.script, clock=ev.VirtualClock()),
            limits=ev.Limits(16, 100, 100, 100),
            per_call=ev.Reservation(10, 10),
            budget_id="learning-budget",
            training_output_id=self.train.output.output_id,
            retained_use=self.source.use,
            materialized_batches=self.batches,
        )
        options.update(changes)
        return Session.materialized_learning(
            self.source.store, VERSION, "learning-run", self.source.snapshot, **options
        )

    def compiled(self) -> Session:
        session = self.admit()
        fixture: Any = importlib.import_module("test_durable_gepa").DurableGEPATests()
        fixture.session = session
        fixture.example = self.train
        fixture.compile_fixture()
        return session

    def test_start_is_read_only_and_does_not_import_or_dispatch(self) -> None:
        before = persisted(self.source.store)
        with (
            patch(
                "refinery.gepa_optimizer.runtime_identity",
                side_effect=AssertionError("runtime"),
            ),
            patch(
                "refinery.dspy_optimizer.framework",
                side_effect=AssertionError("framework"),
            ),
            patch.object(
                ev.ScriptedProvider, "complete", side_effect=AssertionError("effect")
            ),
        ):
            session = self.admit()
        self.assertIs(type(session), Session)
        self.assertEqual(persisted(self.source.store), before)
        self.assertEqual(session.provider.calls, [])
        self.assertIsNone(session.row(session.store.db))

    def test_missing_duplicate_or_unknown_context_is_denied(self) -> None:
        before = persisted(self.source.store)
        for batches in (
            (),
            (self.batches[0],),
            (self.batches[1],),
            (self.batches[0], self.batches[0]),
        ):
            with self.subTest(batches=len(batches)), self.assertRaises(ValueError):
                self.admit(materialized_batches=batches)
        for intent in ("", "resume", "compile", None):
            with self.assertRaises(ValueError):
                self.admit(intent)
        with self.assertRaises(ValueError):
            self.admit(retained_use=None)
        self.assertEqual(persisted(self.source.store), before)

    def test_current_heldout_denial_prevents_start(self) -> None:
        denied = replace(self.batches[1], approve=lambda *_: False)
        before = persisted(self.source.store)
        with self.assertRaises((ValueError, ev.Halt)):
            self.admit(materialized_batches=(self.batches[0], denied))
        self.assertEqual(persisted(self.source.store), before)

    def test_later_callback_withdrawal_is_caught_by_final_training_fence(self) -> None:
        first = self.batches[0]

        def approve(*_: Any) -> bool:
            first.registry.withdraw(
                first.selections[0].item_id,
                approve=first.approve,
                permission_ref=first.permission_ref,
            )
            return True

        second = replace(self.batches[1], approve=approve)
        before = persisted(self.source.store)
        with self.assertRaises((ValueError, ev.Halt)):
            self.admit(materialized_batches=(first, second))
        self.assertEqual(persisted(self.source.store), before)

    def test_callback_cursor_mutation_is_denied(self) -> None:
        provider = ev.ScriptedProvider(self.script, clock=ev.VirtualClock())

        def approve(*_: Any) -> bool:
            provider.calls.append(
                ev.Request("baseline", "out", "v1", "a" * 64, "b" * 64, "c" * 64, 1)
            )
            return True

        second = replace(self.batches[1], approve=approve)
        with self.assertRaises((ValueError, ev.Halt)):
            self.admit(
                provider=provider, materialized_batches=(self.batches[0], second)
            )
        self.assertEqual(
            self.source.store.db.execute("SELECT count(*) FROM experiments").fetchone()[
                0
            ],
            0,
        )

    def test_start_cannot_take_existing_owner_and_resume_cannot_take_interruption(
        self,
    ) -> None:
        with self.assertRaisesRegex(ev.Halt, "not_resumable"):
            self.admit("resume_evaluation")
        session = self.admit()
        session.claim_compile()
        before = persisted(self.source.store)
        with self.assertRaisesRegex(ev.Halt, "already_owned"):
            self.admit()
        with self.assertRaisesRegex(ev.Halt, "not_resumable"):
            self.admit("resume_evaluation")
        self.assertEqual(persisted(self.source.store), before)

    def test_compiled_and_completed_resume_does_not_ask_training_grants(self) -> None:
        calls = []
        allowed = {"training": True}
        original = self.batches

        def approve(descriptor: Any, purpose: str, reference: str) -> bool:
            calls.append(purpose)
            if purpose in {"optimization", "reflection"} and not allowed["training"]:
                raise AssertionError("training must not be reauthorized")
            return bool(original[0].approve(descriptor, purpose, reference))

        self.batches = tuple(replace(b, approve=approve) for b in original)
        session = self.compiled()
        allowed["training"] = False
        calls.clear()
        before = persisted(self.source.store)
        resumed = self.admit("resume_evaluation")
        self.assertEqual(persisted(self.source.store), before)
        self.assertTrue(calls)
        self.assertEqual(set(calls), {"evaluation"})
        probe: Any = importlib.import_module("test_gepa_heldout").HeldoutTests()
        probe.s = resumed
        probe.heldout = self.heldout
        report = probe.evaluate_fixture()
        calls.clear()
        before = persisted(self.source.store)
        completed = self.admit("resume_evaluation")
        self.assertEqual(completed.evaluate(), report)
        self.assertEqual(completed.provider.calls, [])
        self.assertEqual(set(calls), {"evaluation"})
        self.assertEqual(persisted(self.source.store), before)
        self.assertEqual(session.binding, completed.binding)

    def test_evaluating_owner_cannot_be_resumed_or_taken(self) -> None:
        session = self.compiled()
        session.claim_evaluation()
        before = persisted(self.source.store)
        with self.assertRaisesRegex(ev.Halt, "not_resumable"):
            self.admit("resume_evaluation")
        self.assertEqual(persisted(self.source.store), before)

    def test_admission_is_not_cached_grant(self) -> None:
        session = self.admit()
        batch = self.batches[0]
        batch.registry.withdraw(
            batch.selections[0].item_id,
            approve=batch.approve,
            permission_ref=batch.permission_ref,
        )
        with self.assertRaises((ValueError, ev.Halt)):
            session.claim_compile()
        self.assertEqual(session.provider.calls, [])
        self.assertIsNone(session.row(session.store.db))

    def test_resume_with_changed_reference_or_missing_batch_is_denied(self) -> None:
        self.compiled()
        changed = (
            self.batches[0],
            replace(self.batches[1], permission_ref="different-reference"),
        )
        with self.assertRaises(ValueError):
            self.admit("resume_evaluation", materialized_batches=changed)
        with self.assertRaises(ValueError):
            self.admit("resume_evaluation", materialized_batches=(self.batches[0],))
        heldout = self.batches[1]
        heldout.registry.withdraw(
            heldout.selections[0].item_id,
            approve=heldout.approve,
            permission_ref=heldout.permission_ref,
        )
        before = persisted(self.source.store)
        with self.assertRaises((ValueError, ev.Halt)):
            self.admit("resume_evaluation")
        self.assertEqual(persisted(self.source.store), before)

    def test_owned_generic_read_has_no_factory_override(self) -> None:
        session = self.admit()
        session.claim_compile()
        batch = self.batches[0]
        selection = batch.selections[0]
        with self.assertRaises(ev.Halt):
            batch.registry.read(
                selection.item_id,
                selection.source,
                purpose="optimization",
                approve=batch.approve,
                permission_ref=batch.permission_ref,
            )

    def test_wrong_selected_training_and_altered_manifest_rejected(self) -> None:
        before = persisted(self.source.store)
        with self.assertRaises(ValueError):
            self.admit(training_output_id=self.heldout[0].output.output_id)
        changed = (
            self.batches[0],
            replace(self.batches[1], manifest=self.batches[1].manifest + b" "),
        )
        with self.assertRaises(ValueError):
            self.admit(materialized_batches=changed)
        provider = ev.ScriptedProvider(self.script, clock=ev.VirtualClock())
        provider.calls.append(
            ev.Request("baseline", "out", "v1", "a" * 64, "b" * 64, "c" * 64, 1)
        )
        with self.assertRaisesRegex(ValueError, "pristine"):
            self.admit(provider=provider)
        self.assertEqual(persisted(self.source.store), before)

    def test_later_callback_same_owner_seal_invalidates_start(self) -> None:
        from refinery import heldout_policy as policy

        original = self.admit()

        def approve(*_: Any) -> bool:
            with original.store.transaction() as tx:
                policy.enroll(
                    original.store,
                    tx,
                    original.dataset,
                    original.split,
                    owner=original.key,
                    configuration_sha256=original.config,
                )
                policy.seal(
                    original.store,
                    tx,
                    original.dataset,
                    original.split,
                    owner=original.key,
                    configuration_sha256=original.config,
                    candidate_sha256="a" * 64,
                )
            return True

        changed = (self.batches[0], replace(self.batches[1], approve=approve))
        with self.assertRaisesRegex(ev.Halt, "sealed_heldout_cohort"):
            self.admit(materialized_batches=changed)
        self.assertIsNone(original.row(original.store.db))
        self.assertEqual(original.provider.calls, [])
        self.assertEqual(
            original.store.db.execute("SELECT count(*) FROM budgets").fetchone()[0], 0
        )
