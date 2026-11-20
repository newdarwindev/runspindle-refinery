"""Local actual signed lineage materialization; no remote uploads or model calls."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import dataset_contract as c
from refinery.dataset_materialization import DatasetRegistry, prepare
from refinery.evaluation import Halt
from refinery.feedback_collection import collect
from refinery.feedback_projection import RetainedSource


class MaterializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module(
            "test_feedback_projection"
        ).ProjectionTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        use = replace(
            self.h.source.use,
            approve_use=lambda r: r.purpose
            in {
                "feedback_projection",
                "optimization",
                "evaluation",
                "dataset_materialization",
            },
        )
        self.source = replace(
            self.h.source,
            use=use,
            authorize_projection=lambda key, destination: dict(self.h.authority)
            if destination == "local-dataset:synthetic"
            else {},
        )
        self.path = self.h.h.root / "datasets.sqlite"
        self.registry = DatasetRegistry(self.path)
        self.addCleanup(self.registry.close)
        self.approved = True
        self.approve = (
            lambda descriptor, purpose, ref: self.approved
            and ref == "explicit-test-grant"
        )

    def add(self, source: Any = None) -> dict[str, Any]:
        return self.registry.materialize(
            source or self.source,
            "synthetic",
            approve=self.approve,
            permission_ref="explicit-test-grant",
        )

    def read(self, item: dict[str, Any], purpose: str | None = None) -> Any:
        return self.registry.read(
            item["item_id"],
            self.source,
            purpose=purpose
            or {"train": "optimization", "heldout": "evaluation"}[item["split"]],
            approve=self.approve,
            permission_ref="explicit-test-grant",
        )

    def test_shared_unicode_fixture_and_strict_json(self) -> None:
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "contracts/dataset-materialization-v1.fixture.json"
            ).read_bytes()
        )
        self.assertEqual(
            c.binding(
                fixture["dataset"],
                fixture["source_identity_sha256"],
                fixture["input"],
                fixture["output_utf8"].encode(),
                fixture["family_sha256"],
                fixture["split"],
            ),
            fixture["binding"],
        )
        for value in ((1, 2), {1: "alias"}, float("nan"), "\ud800", ["x" * 65537]):
            with self.assertRaises((ValueError, UnicodeError)):
                c.binding("synthetic", "a" * 64, value, b"output", "b" * 64, "train")
        nested: Any = None
        for _ in range(34):
            nested = [nested]
        with self.assertRaises(ValueError):
            c.finite_json(nested)

    def test_exact_bytes_idempotent_reopen_and_fresh_read(self) -> None:
        item = self.add()
        self.assertEqual(self.add(), item)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (1,)
        )
        value, output = self.read(item)
        self.assertEqual(value.encode(), self.h.h.source.content)
        self.assertEqual(output, self.h.h.output.content)
        other = DatasetRegistry(self.path)
        try:
            self.assertEqual(
                other.read(
                    item["item_id"],
                    self.source,
                    purpose={"train": "optimization", "heldout": "evaluation"}[
                        item["split"]
                    ],
                    approve=self.approve,
                    permission_ref="explicit-test-grant",
                ),
                (value, output),
            )
        finally:
            other.close()

    def test_no_content_retained_without_explicit_materialization_grant(self) -> None:
        self.approved = False
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (0,)
        )
        self.approved = True
        denied = replace(
            self.source,
            use=replace(
                self.source.use,
                approve_use=lambda request: request.purpose
                != "dataset_materialization",
            ),
        )
        with self.assertRaises(Halt):
            self.add(denied)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (0,)
        )

    def test_fresh_consent_and_use_authority_required_after_materialization(
        self,
    ) -> None:
        item = self.add()
        self.approved = False
        with self.assertRaises(ValueError):
            self.read(item)
        self.approved = True
        self.source = replace(
            self.source, use=replace(self.source.use, approve_use=lambda _: False)
        )
        with self.assertRaises(ValueError):
            self.read(item)

    def test_locked_split_cannot_be_reinterpreted_for_other_purpose(self) -> None:
        item = self.add()
        wrong = {"train": "evaluation", "heldout": "optimization"}[item["split"]]
        with self.assertRaises(ValueError):
            self.read(item, wrong)
        with self.assertRaises(ValueError):
            self.read(item, "export")

    def test_same_family_input_occurrence_cannot_cross_split(self) -> None:
        item = self.add()
        opposite = None
        for number in range(100):
            snapshot = self.h.h.collect(self.h.selected, seed=f"seed-{number}")
            candidate = replace(self.source, snapshot=snapshot)
            if prepare(candidate, "synthetic").descriptor["split"] != item["split"]:
                opposite = candidate
                break
        self.assertIsNotNone(opposite)
        with self.assertRaisesRegex(ValueError, "crosses split"):
            self.add(opposite)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (1,)
        )

    def test_signed_reaction_removal_denies_existing_content_use(self) -> None:
        item = self.add()
        raw = self.h.h.reaction(True)
        self.h.h.app.react(raw, self.h.h.headers(raw))
        with self.assertRaises(ValueError):
            self.read(item)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (1,)
        )

    def test_target_change_during_permission_rolls_back_content(self) -> None:
        calls = 0

        def revoke(descriptor: Any, purpose: str, reference: str) -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.h.h.invalidate_target("message_deleted")
            return True

        self.approve = revoke
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (0,)
        )

    def co_selected_feedback(self) -> tuple[RetainedSource, Any]:
        original = self.h.h
        other: Any = importlib.import_module(
            "test_thumbs_up_collection"
        ).ReactionCollectionTests()
        other.actor_id = "USECOND"
        other.event_prefix = "EvSecond"
        other.feedback_path = original.root / "feedback.sqlite"
        other.setUp()
        self.addCleanup(other.doCleanups)
        selected = other.select()
        self.assertEqual(original.target, other.target)
        self.assertEqual(original.material, other.material)
        snapshot = collect(
            original.store,
            original.dep,
            original.app.store,
            (self.source.selection, selected),
            (),
            seed="two-actors",
            collection_ref="collection",
            training_ref="training",
            resolve_material=lambda _: original.material,
            verify_material=self.source.use.verify_material,
            approve_collection=lambda *_: True,
            approve_training=lambda *_: True,
        )
        return replace(self.source, snapshot=snapshot), other

    def revoke_co_selected_at_final_observation(
        self, source: RetainedSource, other: Any
    ) -> RetainedSource:
        calls = 0

        def authorize(key: str, destination: str) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            # Each prepare observes policy twice. Invalidate on the last read.
            if calls == 4:
                raw = other.reaction(True)
                other.app.react(raw, other.headers(raw))
            return source.authorize_projection(key, destination)

        return replace(source, authorize_projection=authorize)

    def assert_co_selected_removal(self, other: Any) -> None:
        original = self.h.h
        self.assertEqual(
            original.app.store.evidence(original.app.namespace, original.feedback_id)[
                "state"
            ],
            "active",
        )
        self.assertEqual(
            other.app.store.evidence(other.app.namespace, other.feedback_id)["state"],
            "invalidated",
        )

    def test_co_selected_removal_during_final_observation_rolls_back_content(
        self,
    ) -> None:
        source, other = self.co_selected_feedback()
        with self.assertRaisesRegex(ValueError, "batch feedback changed"):
            self.add(self.revoke_co_selected_at_final_observation(source, other))
        self.assert_co_selected_removal(other)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (0,)
        )

    def test_co_selected_removal_during_final_observation_denies_read(self) -> None:
        source, other = self.co_selected_feedback()
        item = self.add(source)
        self.source = self.revoke_co_selected_at_final_observation(source, other)
        with self.assertRaisesRegex(ValueError, "batch feedback changed"):
            self.read(item)
        self.assert_co_selected_removal(other)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (1,)
        )

    def test_permanent_local_withdrawal_does_not_claim_bytes_erased(self) -> None:
        item = self.add()
        self.registry.withdraw(
            item["item_id"], approve=self.approve, permission_ref="explicit-test-grant"
        )
        with self.assertRaises(ValueError):
            self.read(item)
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (1,)
        )
        # Behavioral mutation demonstrates that the guarded-read denial matters.
        with patch.object(self.registry, "available", lambda _: None):
            self.assertEqual(self.read(item)[1], self.h.h.output.content)

    def test_empty_versioned_schema_and_immutable_content_denied(self) -> None:
        for version in (1, 99):
            path = self.h.h.root / f"emptied-dataset-{version}.sqlite"
            db = sqlite3.connect(path)
            db.execute(f"PRAGMA user_version={version}")
            db.close()
            with self.assertRaises(ValueError):
                DatasetRegistry(path)
        item = self.add()
        with self.assertRaises(sqlite3.IntegrityError):
            self.registry.db.execute(
                "UPDATE items SET output=? WHERE item_id=?", (b"wrong", item["item_id"])
            )
        self.assertEqual(self.read(item)[1], self.h.h.output.content)

    def enroll(self) -> None:
        from refinery.heldout_policy import enroll

        data, split = self.source.store.lineage(self.source.snapshot)
        with self.source.store.transaction() as tx:
            enroll(
                self.source.store,
                tx,
                data,
                split,
                owner="synthetic-owner",
                configuration_sha256="a" * 64,
            )

    def test_enrolled_cohort_denied_before_materialization_and_read(self) -> None:
        item = self.add()
        self.enroll()
        with self.assertRaises(Halt):
            self.read(item)
        with self.assertRaises(Halt):
            self.add()

    def test_final_callback_enrollment_rolls_back_new_content(self) -> None:
        calls = 0

        def grant(descriptor: Any, purpose: str, ref: str) -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.enroll()
            return True

        self.approve = grant
        with self.assertRaises(Halt):
            self.add()
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (0,)
        )

    def test_private_regular_storage_and_no_schema_prewrite(self) -> None:
        unsafe = self.h.h.root / "shared.sqlite"
        unsafe.write_bytes(b"")
        unsafe.chmod(0o644)
        with self.assertRaises(ValueError):
            DatasetRegistry(unsafe)
        self.assertEqual(unsafe.read_bytes(), b"")
        link = self.h.h.root / "linked.sqlite"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            DatasetRegistry(link)
        future = self.h.h.root / "future-private.sqlite"
        fd = os.open(future, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        db = sqlite3.connect(future)
        db.execute("PRAGMA user_version=99")
        db.close()
        before = future.read_bytes()
        with self.assertRaises(ValueError):
            DatasetRegistry(future)
        self.assertEqual(future.read_bytes(), before)

    def test_receipt_preserves_source_provenance_not_current_permission(self) -> None:
        item = self.add()
        row = self.registry.db.execute(
            "SELECT receipt FROM items WHERE item_id=?", (item["item_id"],)
        ).fetchone()
        receipt = json.loads(row[0])
        self.assertEqual(receipt["snapshot_sha256"], self.source.snapshot)
        self.assertEqual(
            receipt["selected_event_sha256"], self.source.selection.event_sha256
        )
        self.assertEqual(
            receipt["collection_receipt_sha256"],
            self.source.store.get("lineage", self.source.snapshot)[
                "collection_evidence"
            ],
        )
        self.assertEqual(
            receipt["permission_scope"], "historical_not_current_authority"
        )
        self.approved = False
        with self.assertRaises(ValueError):
            self.read(item)

    def test_full_registry_identical_retry_remains_idempotent(self) -> None:
        item = self.add()
        with patch("refinery.dataset_materialization.MAX_ITEMS", 1):
            self.assertEqual(self.add(), item)
        self.assertEqual(
            self.registry.db.execute("SELECT COUNT(*) FROM items").fetchone(), (1,)
        )

    def test_heldout_evaluation_only_authority_is_not_optimization_permission(
        self,
    ) -> None:
        for number in range(100):
            snapshot = self.h.h.collect(self.h.selected, seed=f"evaluation-{number}")
            candidate = replace(self.source, snapshot=snapshot)
            if prepare(candidate, "synthetic").descriptor["split"] == "heldout":
                self.source = candidate
                break
        self.source = replace(
            self.source,
            use=replace(
                self.source.use,
                approve_use=lambda request: request.purpose
                in {"feedback_projection", "dataset_materialization", "evaluation"},
            ),
        )
        item = self.add()
        self.assertEqual(item["split"], "heldout")
        self.assertEqual(self.read(item, "evaluation")[1], self.h.h.output.content)
        with self.assertRaises(ValueError):
            self.read(item, "optimization")

    def test_database_and_sqlite_sidecars_stay_private(self) -> None:
        self.add()
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
