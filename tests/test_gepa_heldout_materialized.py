"""Actual signed mixed batches/owner/dispatch, manual compile/eval receipts only."""

from __future__ import annotations

import importlib
import unittest
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery import gepa_optimizer as gepa
from refinery.durable_dspy import Session
from refinery.lineage import digest
from refinery.materialized_use import BoundBatch
from refinery.optimization import validate_dataset
from refinery.storage import VERSION, pack


class MaterializedHeldoutTests(unittest.TestCase):
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
        self.s = self.make()
        fixture: Any = importlib.import_module("test_durable_gepa").DurableGEPATests()
        fixture.session = self.s
        fixture.example = self.train
        self.compiled = fixture.compile_fixture()

    def make(self, **changes: Any) -> Session:
        options = dict(
            provider=ev.ScriptedProvider(self.script, clock=ev.VirtualClock()),
            limits=ev.Limits(16, 100, 100, 100),
            per_call=ev.Reservation(10, 10),
            budget_id="mixed-budget",
            strategy="gepa",
            training_output_id=self.train.output.output_id,
            retained_use=self.source.use,
            materialized_batches=self.batches,
        )
        options.update(changes)
        return Session(
            self.source.store, VERSION, "mixed-gepa", self.source.snapshot, **options
        )

    def test_exact_mixed_owner_evaluation_and_withdrawal_replay_denial(self) -> None:
        probe: Any = importlib.import_module("test_gepa_heldout").HeldoutTests()
        probe.s = self.s
        probe.heldout = self.heldout
        report = probe.evaluate_fixture()
        resumed = self.make()
        with (
            patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime")
            ),
            patch(
                "refinery.dspy_optimizer.framework",
                side_effect=AssertionError("framework"),
            ),
        ):
            self.assertEqual(resumed.evaluate(), report)
            measured = resumed.measured_materialized_heldout()
            with_cohort = resumed.measured_materialized_heldout(include_cohort=True)
        batch = self.batches[1]
        self.assertEqual(
            measured["version"], "runspindle.gepa-materialized-measurement.v1"
        )
        self.assertEqual(measured["heldout_manifest_sha256"], digest(batch.manifest))
        self.assertEqual(len(self.heldout), 1)
        output = self.heldout[0].output
        case = digest(pack((output.output_id, output.revision)))
        self.assertEqual(
            measured["cases"], [{"case": case, "item_id": batch.selections[0].item_id}]
        )
        self.assertEqual(measured["measurement"]["rows"]["candidate"][0]["case"], case)
        cohort = with_cohort["measurement"]["cohort"]
        self.assertEqual(cohort["heldout_cases"], [case])
        self.assertEqual(
            cohort["training_cases"],
            sorted(
                digest(pack((item.output.output_id, item.output.revision)))
                for item in resumed.dataset.examples
                if resumed.split.partition(item.source.family_id) == "train"
            ),
        )
        self.assertEqual(with_cohort["cases"], measured["cases"])
        self.assertEqual(
            with_cohort["heldout_manifest_sha256"], measured["heldout_manifest_sha256"]
        )
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(len(self.s.provider.calls), 3 + 2 * len(self.heldout))
        # No generic cohort-owner override is introduced for either split.
        for batch, purpose in zip(self.batches, ("optimization", "evaluation")):
            selection = batch.selections[0]
            with self.assertRaises(ev.Halt):
                batch.registry.read(
                    selection.item_id,
                    selection.source,
                    purpose=purpose,
                    approve=batch.approve,
                    permission_ref=batch.permission_ref,
                )
        before = tuple(self.source.store.db.execute("SELECT * FROM budgets"))
        heldout_batch = self.batches[1]
        heldout_batch.registry.withdraw(
            heldout_batch.selections[0].item_id,
            approve=heldout_batch.approve,
            permission_ref=heldout_batch.permission_ref,
        )
        with self.assertRaises((ValueError, ev.Halt)):
            resumed.evaluate()
        with self.assertRaises((ValueError, ev.Halt)):
            resumed.measured_materialized_heldout()
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(
            tuple(self.source.store.db.execute("SELECT * FROM budgets")), before
        )

    def test_paired_mapping_uses_the_retained_optimization_batch(self) -> None:
        probe: Any = importlib.import_module("test_gepa_heldout").HeldoutTests()
        probe.s, probe.heldout = self.s, self.heldout
        probe.evaluate_fixture()
        resumed = self.make()
        scope = BoundBatch.scope
        opened = []

        def evaluation_only(batch: BoundBatch) -> Any:
            opened.append(batch.purpose)
            if batch.purpose != "evaluation":
                self.fail("paired read reopened training")
            return scope(batch)

        with (
            patch.object(BoundBatch, "scope", evaluation_only),
            patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime")
            ),
            patch(
                "refinery.dspy_optimizer.framework",
                side_effect=AssertionError("framework"),
            ),
        ):
            report = resumed.measured_materialized_cohorts()
            old = resumed.measured_materialized_heldout(include_cohort=True)
        self.assertEqual(opened, ["evaluation", "evaluation"])
        self.assertEqual(
            report["version"], "runspindle.gepa-materialized-cohort-measurement.v1"
        )
        training, heldout = self.batches
        case = digest(pack((self.train.output.output_id, self.train.output.revision)))
        self.assertEqual(report["training_manifest_sha256"], digest(training.manifest))
        self.assertEqual(
            report["training_cases"],
            [{"case": case, "item_id": training.selections[0].item_id}],
        )
        self.assertEqual(report["measurement"]["cohort"]["training_cases"], [case])
        self.assertEqual(report["heldout_manifest_sha256"], digest(heldout.manifest))
        for field in ("measurement", "heldout_manifest_sha256", "cases"):
            self.assertEqual(report[field], old[field])
        self.assertEqual(resumed.provider.calls, [])

        # A retained selection cannot be stretched to cover an unmatched roster.
        with self.assertRaisesRegex(
            ValueError, "exact retained materialized training cohort"
        ):
            resumed._retained_training_cases((self.train, self.heldout[0]))

        heldout.registry.withdraw(
            heldout.selections[0].item_id,
            approve=heldout.approve,
            permission_ref=heldout.permission_ref,
        )
        with self.assertRaises((ValueError, ev.Halt)):
            resumed.measured_materialized_cohorts()
        self.assertEqual(resumed.provider.calls, [])

    def test_missing_or_changed_heldout_batch_cannot_claim(self) -> None:
        missing = self.make(materialized_batches=(self.batches[0],))
        with self.assertRaises(ValueError):
            missing.claim_evaluation()
        self.assertEqual(missing.provider.calls, [])
        from dataclasses import replace

        changed = self.make(
            materialized_batches=(
                self.batches[0],
                replace(self.batches[1], permission_ref="different-grant"),
            )
        )
        with self.assertRaises(ValueError):
            changed.claim_evaluation()
        self.assertEqual(changed.provider.calls, [])
