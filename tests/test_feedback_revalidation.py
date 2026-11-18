"""Constructed historical receipts, real stores, explicit fresh consent only."""

import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.feedback_use import RetainedFeedbackUse, UseRequest
from refinery.lineage import digest
from refinery.storage import pack

fixture = importlib.import_module("test_feedback_collection")
OLD = "c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d"


class RevalidationTests(unittest.TestCase):
    setUp: Any = fixture.CollectionTests.setUp
    event: Any = fixture.CollectionTests.event
    approve: Any = fixture.CollectionTests.approve
    collect: Any = fixture.CollectionTests.collect
    store: Any
    dep: Any
    feedback: Any
    root: Path
    raw: bytes
    delivery: Any
    payload: Any
    material: Any
    approvals: list[Any]

    def historical(self, **changes: Any) -> str:
        current = self.collect()
        snapshot = self.store.get("lineage", current)
        receipt = self.store.get("feedback_collection", snapshot["collection_evidence"])
        receipt = dict(receipt, dependency_commit=OLD, **changes)
        receipt_sha = digest(pack(receipt))
        snapshot = dict(snapshot, collection_evidence=receipt_sha)
        old = digest(pack(snapshot))
        with self.store.transaction() as tx:
            self.store.put(tx, "feedback_collection", receipt_sha, receipt)
            self.store.put(tx, "lineage", old, snapshot)
        return old

    def migrate(self, old: str, **changes: Any) -> str:
        args = dict(
            prior_snapshot_sha256=old,
            collection_ref="fresh-collection",
            training_ref="fresh-training",
        )
        args.update(changes)
        return str(self.collect(**args))

    def test_fresh_consent_preserves_prior_and_creates_atomic_link(self) -> None:
        old = self.historical()
        before = pack(self.store.get("lineage", old))
        self.approvals.clear()
        new = self.migrate(old)
        self.assertNotEqual(old, new)
        self.assertEqual(pack(self.store.get("lineage", old)), before)
        self.assertEqual(self.store.lineage(old), self.store.lineage(new))
        self.assertEqual(
            {ref for _, ref in self.approvals}, {"fresh-collection", "fresh-training"}
        )
        self.assertEqual(self.migrate(old), new)
        rows = self.store.db.execute(
            "SELECT identity FROM objects WHERE kind='feedback_revalidation'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        link = self.store.get("feedback_revalidation", rows[0][0])
        self.assertEqual(
            (link["prior_snapshot_sha256"], link["snapshot_sha256"]), (old, new)
        )
        code = "from refinery.storage import Store; import sys,json; s=Store(sys.argv[1]); print(json.dumps([s.lineage(sys.argv[2]) == s.lineage(sys.argv[3]), s.db.execute(\"SELECT count(*) FROM objects WHERE kind='feedback_revalidation'\").fetchone()[0]])); s.close()"
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.root / "refinery.sqlite"), old, new],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True, 1])

    def test_prior_ae5_pin_requires_fresh_consent_and_preserves_bytes(self) -> None:
        with patch(__name__ + ".OLD", "ae5ac216b45ca413283e4200c0a4a61054998b58"):
            old = self.historical()
        before = pack(self.store.get("lineage", old))
        with self.assertRaises(ValueError):
            self.migrate(old, approve_training=lambda *args: False)
        new = self.migrate(old)
        self.assertNotEqual(new, old)
        self.assertEqual(pack(self.store.get("lineage", old)), before)
        self.assertEqual(self.store.lineage(old), self.store.lineage(new))

    def test_old_receipt_does_not_gain_use_permission(self) -> None:
        old = self.historical()
        self.migrate(old)
        snapshot = self.store.get("lineage", old)
        policy = RetainedFeedbackUse(
            self.dep,
            self.feedback,
            verify_material=lambda *args: True,
            approve_use=lambda request: True,
        )
        with self.assertRaisesRegex(ValueError, "collection association changed"):
            policy.verify(
                self.store,
                UseRequest(
                    old,
                    snapshot["batch_sha256"],
                    snapshot["collection_evidence"],
                    "evaluation",
                    "0" * 64,
                    "1" * 64,
                ),
            )

    def test_fresh_permission_denial_does_not_change_store(self) -> None:
        old = self.historical()
        before = tuple(self.store.db.iterdump())
        for field in ("approve_collection", "approve_training", "verify_material"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.migrate(old, **{field: lambda *args: False})
            self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_changed_feedback_cannot_migrate_prior_dataset(self) -> None:
        old = self.historical()
        raw = self.event(2, judgment="negative")
        self.feedback.ingest(raw, digest(raw), self.delivery, self.payload)
        before = tuple(self.store.db.iterdump())
        with self.assertRaisesRegex(ValueError, "revalidation content changed"):
            self.migrate(old)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_changed_seed_and_provenance_fail_closed(self) -> None:
        old = self.historical()
        before = tuple(self.store.db.iterdump())
        with self.assertRaises(ValueError):
            self.migrate(old, seed="other-seed")
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        current = self.collect()
        with self.assertRaisesRegex(ValueError, "unsupported feedback revalidation"):
            self.migrate(current)
        bad = self.historical(scope="forged")
        before = tuple(self.store.db.iterdump())
        with self.assertRaisesRegex(ValueError, "content changed"):
            self.migrate(bad)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_corrupted_historical_identity_is_not_migrated(self) -> None:
        old = self.historical()
        value = self.store.get("lineage", old)
        with self.store.transaction() as tx:
            self.store.put(tx, "lineage", "0" * 64, value)
        before = tuple(self.store.db.iterdump())
        with self.assertRaises(ValueError):
            self.migrate("0" * 64)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_repeat_after_conflict_rolls_back_and_removed_guard_is_detected(
        self,
    ) -> None:
        old = self.historical()
        expected = self.migrate(old)
        snapshot = self.store.get("lineage", old)
        conflict = replace(snapshot["batch"][2][0], kind="negative")
        with self.store.transaction() as tx:
            self.store.put(tx, "feedback", digest(pack(conflict)), conflict)
        before = tuple(self.store.db.iterdump())
        with self.assertRaisesRegex(ValueError, "revalidation dataset changed"):
            self.migrate(old)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        original = self.store.commit_lineage

        def mutant(*args: Any, **kwargs: Any) -> Any:
            # Deliberately remove actual-proposal binding while retaining the
            # prior preexisting-object check. It must not qualify a changed run.
            kwargs.pop("expected_snapshot_sha256")
            return original(*args, **kwargs)

        with patch.object(self.store, "commit_lineage", mutant):
            changed = self.migrate(old)
        self.assertNotEqual(changed, expected)
        self.assertNotEqual(self.store.lineage(changed), self.store.lineage(old))
        link_id = self.store.db.execute(
            "SELECT identity FROM objects WHERE kind='feedback_revalidation'"
        ).fetchone()[0]
        self.assertEqual(
            self.store.get("feedback_revalidation", link_id)["snapshot_sha256"],
            expected,
        )

    def test_guarded_historical_receipts_keep_source_fences(self) -> None:
        source = importlib.import_module("test_source_feedback")
        with tempfile.TemporaryDirectory() as temporary:
            h = source.SourceFixture(Path(temporary))
            try:
                h.ingest()
                current = h.collect()
                snapshot = h.store.get("lineage", current)
                receipt = h.store.get(
                    "feedback_collection", snapshot["collection_evidence"]
                )
                receipt = dict(receipt, dependency_commit=OLD)
                receipt_sha = digest(pack(receipt))
                snapshot = dict(snapshot, collection_evidence=receipt_sha)
                old = digest(pack(snapshot))
                with h.store.transaction() as tx:
                    h.store.put(tx, "feedback_collection", receipt_sha, receipt)
                    h.store.put(tx, "lineage", old, snapshot)
                new = h.collect(
                    prior_snapshot_sha256=old,
                    collection_ref="fresh",
                    training_ref="fresh",
                )
                h.verify_use(new)
                h.fence()
                before = tuple(h.store.db.iterdump())
                with self.assertRaises(ValueError):
                    h.collect(
                        prior_snapshot_sha256=old,
                        collection_ref="again",
                        training_ref="again",
                    )
                self.assertEqual(tuple(h.store.db.iterdump()), before)
            finally:
                h.close()

    def test_cancellation_inside_commit_rolls_back_link_and_snapshot(self) -> None:
        old = self.historical()
        before = tuple(self.store.db.iterdump())
        calls = 0

        def cancel(sha: str, ref: str) -> bool:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise KeyboardInterrupt("synthetic cancellation")
            return True

        with self.assertRaises(KeyboardInterrupt):
            self.migrate(old, approve_training=cancel)
        self.assertEqual(calls, 3)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_historical_callback_mutation_rolls_back(self) -> None:
        old = self.historical()
        before = tuple(self.store.db.iterdump())
        calls = 0

        def mutate(sha: str, ref: str) -> bool:
            nonlocal calls
            calls += 1
            if calls == 3:
                self.store.db.execute(
                    "DELETE FROM objects WHERE kind='lineage' AND identity=?", (old,)
                )
            return True

        with self.assertRaises(ValueError):
            self.migrate(old, approve_training=mutate)
        self.assertEqual(calls, 3)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
