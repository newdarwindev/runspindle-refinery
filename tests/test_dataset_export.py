"""Actual signed local authority with injected HTTP, never remote uploads."""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from refinery import dataset_contract as c
from refinery import heldout_policy
from refinery.dataset_export import DatasetProjection, Destination, Export, safe_content
from refinery.dataset_materialization import DatasetRegistry
from refinery.feedback_use import UseDenied
from refinery.langfuse_projection import Response
from refinery.storage import Store


class HTTP:
    origin = "https://synthetic.invalid"

    def __init__(self, destination: Destination):
        self.destination = destination
        self.item: Any = None
        self.posts = 0
        self.lost = False
        self.hidden = False
        self.wrong = False
        self.callback: Any = None

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout: int,
        max_bytes: int,
    ) -> Response:
        if self.callback is not None:
            self.callback(method, path)
        if timeout != 10 or max_bytes != 262144:
            raise AssertionError("bounds")
        if method == "POST":
            self.posts += 1
            self.item = json.loads(body or b"")
            self.item.update(
                datasetId=self.destination.dataset_id,
                sourceTraceId=None,
                sourceObservationId=None,
                mediaReferences=[],
            )
            if self.lost:
                raise TimeoutError("lost acknowledgement")
            value = self.item
        elif path.startswith("/api/public/v2/datasets/"):
            value = dict(
                projectId="wrong" if self.wrong else self.destination.project_id,
                id=self.destination.dataset_id,
                name=self.destination.dataset_name,
                metadata=dict(runspindle_dataset_split=self.destination.split),
            )
        elif self.item is None or self.hidden:
            return Response(404, b"{}")
        else:
            value = self.item
        return Response(200, c.canonical(value))


class DatasetExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module(
            "test_dataset_materialization"
        ).MaterializationTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        prior = self.h.source.use.approve_use
        use = replace(
            self.h.source.use,
            approve_use=lambda r: r.purpose == "dataset_export" or prior(r),
        )
        self.source = replace(self.h.source, use=use)
        self.item = self.h.add(self.source)
        self.dest = Destination(
            HTTP.origin, "project", "dataset-id", "remote_dataset", self.item["split"]
        )
        self.allow = True
        self.requests: list[Any] = []

        def approve(request: dict[str, Any], purpose: str, reference: str) -> bool:
            self.requests.append((request, purpose))
            return (
                self.allow
                and reference == "export-grant"
                and purpose in {"dataset_export", "dataset_export_revoke"}
            )

        self.approve = approve
        self.export = self.make()
        self.http = HTTP(self.dest)
        self.adapter = DatasetProjection(self.http, self.dest)

    def make(self, **changes: Any) -> Export:
        options: Any = dict(approve=self.approve, permission_ref="export-grant")
        options.update(changes)
        return Export(
            self.source.store,
            self.h.registry,
            self.source,
            self.item["item_id"],
            self.dest,
            **options,
        )

    def test_exact_export_readback_no_raw_extra_records_and_current_grant(self) -> None:
        self.assertEqual(self.export.dispatch(self.adapter), "delivered")
        self.assertEqual(self.http.posts, 1)
        descriptor, input_value, output = self.h.registry.export_read(
            self.item["item_id"],
            self.source,
            destination=self.dest.value(),
            approve=self.approve,
            permission_ref="export-grant",
        )
        self.assertEqual(self.http.item["input"], input_value)
        self.assertEqual(self.http.item["expectedOutput"].encode(), output)
        self.assertEqual(self.http.item["metadata"]["runspindle_dataset"], descriptor)
        self.assertEqual(self.export.dispatch(self.adapter), "delivered")
        self.assertEqual(self.http.posts, 1)
        for kind, payload in self.source.store.db.execute(
            "SELECT kind,payload FROM objects WHERE kind LIKE 'dataset_export_%'"
        ):
            self.assertNotIn(b"expectedOutput", payload)
            self.assertNotIn(output, payload)
        self.allow = False
        with self.assertRaises(ValueError):
            self.export.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 1)

    def test_lost_ack_retains_attempt_and_reconciles_without_repost(self) -> None:
        self.http.lost = True
        with self.assertRaises(TimeoutError):
            self.export.dispatch(self.adapter)
        self.assertIsNotNone(self.export.record("attempt"))
        self.assertIsNone(self.export.record("readback"))
        self.http.hidden = True
        self.assertEqual(self.make().dispatch(self.adapter), "uncertain")
        self.assertEqual(self.http.posts, 1)
        self.http.hidden = False
        self.assertEqual(self.make().dispatch(self.adapter), "delivered")
        self.assertEqual(self.http.posts, 1)

    def test_wrong_project_or_split_denies_before_post(self) -> None:
        self.http.wrong = True
        with self.assertRaises(ValueError):
            self.export.dispatch(self.adapter)
        wrong = replace(
            self.dest, split="heldout" if self.dest.split == "train" else "train"
        )
        export = Export(
            self.source.store,
            self.h.registry,
            self.source,
            self.item["item_id"],
            wrong,
            approve=self.approve,
            permission_ref="export-grant",
        )
        with self.assertRaises(ValueError):
            export.capture()
        self.assertEqual(self.http.posts, 0)

    def test_sticky_revoke_blocks_delayed_claimed_writer(self) -> None:
        def checkpoint(stage: str) -> None:
            self.assertEqual(stage, "after_dataset_export_attempt")
            self.export.revoke(approve=self.approve, permission_ref="export-grant")

        delayed = self.make(checkpoint=checkpoint)
        with self.assertRaisesRegex(ValueError, "locally revoked"):
            delayed.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 0)
        self.assertIsNotNone(self.export.record("attempt"))
        with self.assertRaisesRegex(ValueError, "locally revoked"):
            self.make().dispatch(self.adapter)

    def test_registry_withdrawal_in_http_callback_denies_write(self) -> None:
        def withdraw(method: str, path: str) -> None:
            if method == "GET":
                self.h.registry.withdraw(
                    self.item["item_id"],
                    approve=self.h.approve,
                    permission_ref="explicit-test-grant",
                )

        self.http.callback = withdraw
        with self.assertRaises(ValueError):
            self.export.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 0)

    def test_revocation_after_remote_write_never_settles(self) -> None:
        def withdraw(method: str, path: str) -> None:
            if method == "POST":
                self.h.registry.withdraw(
                    self.item["item_id"],
                    approve=self.h.approve,
                    permission_ref="explicit-test-grant",
                )

        self.http.callback = withdraw
        with self.assertRaises(ValueError):
            self.export.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 1)
        self.assertIsNone(self.export.record("readback"))
        self.assertIsNotNone(
            self.http.item
        )  # Local denial never claims remote erasure.

    def test_bound_destination_mutation_from_authority_denied(self) -> None:
        destination = self.dest.value()

        def mutate(*_: Any) -> bool:
            destination["project_id"] = "changed"
            return True

        with self.assertRaises(ValueError):
            self.h.registry.export_read(
                self.item["item_id"],
                self.source,
                destination=destination,
                approve=mutate,
                permission_ref="export-grant",
            )

    def test_normalization_media_and_remote_conflicts_rejected(self) -> None:
        for value in (
            "nul\x00",
            "newline\n",
            "\x85",
            "@@@langfuseMedia:type=image/png|id=x@@@",
        ):
            with self.assertRaises(ValueError):
                safe_content(value)
        safe_content({"Привет": ["🙂", "café"]})
        self.export.dispatch(self.adapter)
        mutations: tuple[tuple[str, Any], ...] = (
            ("input", "different"),
            ("expectedOutput", "normalized"),
            ("datasetId", "wrong"),
            ("mediaReferences", [{"url": "not-fetched"}]),
            ("status", "ARCHIVED"),
        )
        for key, value in mutations:
            original = self.http.item[key]
            self.http.item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.export.dispatch(self.adapter)
            self.http.item[key] = original
        self.assertEqual(self.http.posts, 1)

    def test_enrolled_cohort_never_borrows_optimizer_owner(self) -> None:
        dataset, split = self.source.store.lineage(self.source.snapshot)
        with self.source.store.transaction() as tx:
            heldout_policy.enroll(
                self.source.store,
                tx,
                dataset,
                split,
                owner="existing-owner",
                configuration_sha256="a" * 64,
            )
        with self.assertRaises(Exception):
            self.export.capture()
        self.assertEqual(self.http.posts, 0)

    def test_two_claimants_only_one_first_write(self) -> None:
        rival = self.make()
        results = []

        def checkpoint(_: str) -> None:
            results.append(rival.dispatch(self.adapter))

        winner = self.make(checkpoint=checkpoint)
        self.assertEqual(winner.dispatch(self.adapter), "delivered")
        self.assertEqual(results, ["uncertain"])
        self.assertEqual(self.http.posts, 1)

    def test_reopen_attempt_state_no_blind_repost(self) -> None:
        self.http.lost = True
        with self.assertRaises(TimeoutError):
            self.export.dispatch(self.adapter)
        with Store(self.source.store.path) as store:
            source = replace(self.source, store=store)
            reopened = Export(
                store,
                self.h.registry,
                source,
                self.item["item_id"],
                self.dest,
                approve=self.approve,
                permission_ref="export-grant",
            )
            self.http.hidden = True
            self.assertEqual(reopened.dispatch(self.adapter), "uncertain")
            self.assertEqual(self.http.posts, 1)

    def test_real_concurrent_first_claim_uses_independent_local_connections(
        self,
    ) -> None:
        barrier = threading.Barrier(2)
        # One pinned Tether verifier deliberately rejects overlapping verify().
        # Serialize only initial authorization, then race independent Store claims.
        admission_lock = threading.Lock()
        admission_state = threading.local()
        lock = threading.Lock()
        seen: set[int] = set()
        original = self.http.request

        def request(
            method: str, path: str, body: bytes | None, *, timeout: int, max_bytes: int
        ) -> Response:
            result = original(method, path, body, timeout=timeout, max_bytes=max_bytes)
            if method == "GET" and path.startswith("/api/public/dataset-items/"):
                ident = threading.get_ident()
                with lock:
                    first = ident not in seen
                    seen.add(ident)
                if first:
                    admission_state.held = False
                    admission_lock.release()
                    barrier.wait(timeout=10)
            return result

        setattr(self.http, "request", request)
        feedback_path = self.source.use.tether_store.db.execute(
            "PRAGMA database_list"
        ).fetchone()[2]
        feedback_type = self.source.use.dependency.modules[
            "tether.feedback"
        ].FeedbackStore

        def worker() -> str:
            admission_lock.acquire()
            admission_state.held = True
            try:
                return admitted_worker()
            finally:
                if admission_state.held:
                    admission_lock.release()

        def admitted_worker() -> str:
            with Store(self.source.store.path) as store:
                feedback = feedback_type(feedback_path, None)
                registry = DatasetRegistry(self.h.path)
                try:
                    source = replace(
                        self.source,
                        store=store,
                        use=replace(self.source.use, tether_store=feedback),
                    )
                    export = Export(
                        store,
                        registry,
                        source,
                        self.item["item_id"],
                        self.dest,
                        approve=self.approve,
                        permission_ref="export-grant",
                    )
                    return export.dispatch(self.adapter)
                finally:
                    registry.close()
                    feedback.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            results = []
            errors = []
            for future in futures:
                try:
                    results.append(future.result(timeout=20))
                except Exception as exc:
                    errors.append((type(exc).__name__, str(exc)))
            self.assertEqual(errors, [])
        self.assertEqual(sorted(results), ["delivered", "uncertain"])
        self.assertEqual(self.http.posts, 1)

    def test_preexisting_readback_never_authorizes_recreation(self) -> None:
        body = self.export.body()
        self.adapter.put(body)
        self.assertEqual(self.export.dispatch(self.adapter), "delivered")
        self.assertIsNone(self.export.record("attempt"))
        self.http.item = None
        self.assertEqual(self.export.dispatch(self.adapter), "uncertain")
        self.assertEqual(self.http.posts, 1)

    def test_export_grant_does_not_substitute_retained_use_or_complete_destination(
        self,
    ) -> None:
        source = replace(
            self.source,
            use=replace(
                self.source.use, approve_use=lambda r: r.purpose != "dataset_export"
            ),
        )
        denied = Export(
            source.store,
            self.h.registry,
            source,
            self.item["item_id"],
            self.dest,
            approve=self.approve,
            permission_ref="export-grant",
        )
        with self.assertRaises(Exception):
            denied.capture()
        with self.assertRaises(ValueError):
            self.h.registry.export_read(
                self.item["item_id"],
                self.source,
                destination={"split": self.dest.split},
                approve=self.approve,
                permission_ref="export-grant",
            )
        self.assertEqual(self.http.posts, 0)

    def test_callback_export_binding_mutation_cannot_replace_guard(self) -> None:
        def changed(method: str, path: str) -> None:
            self.export.permission_ref = "other"
            setattr(self.export, "check", lambda: None)

        self.http.callback = changed
        with self.assertRaises(ValueError):
            self.export.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 0)

    def test_visible_remote_item_does_not_hide_conflicting_local_attempt(self) -> None:
        body = self.export.body()
        self.adapter.put(body)
        self.export.capture()
        with self.source.store.transaction() as tx:
            self.source.store.put(
                tx,
                "dataset_export_attempt",
                self.export.key,
                dict(intent_sha256="f" * 64),
            )
        with self.assertRaisesRegex(ValueError, "attempt binding"):
            self.export.dispatch(self.adapter)
        self.assertIsNone(self.export.record("readback"))
        self.assertEqual(self.http.posts, 1)

    def test_dataset_split_metadata_is_required_before_content_post(self) -> None:
        original = self.http.request

        def request(
            method: str, path: str, body: bytes | None, *, timeout: int, max_bytes: int
        ) -> Response:
            result = original(method, path, body, timeout=timeout, max_bytes=max_bytes)
            if path.startswith("/api/public/v2/datasets/"):
                value = json.loads(result.body)
                value["metadata"] = {}
                return Response(200, c.canonical(value))
            return result

        setattr(self.http, "request", request)
        with self.assertRaisesRegex(ValueError, "split binding"):
            self.export.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 0)

    def test_copied_feedback_authority_cannot_reopen_existing_intent(self) -> None:
        self.export.capture()
        original = self.source.use.tether_store
        path = Path(original.db.execute("PRAGMA database_list").fetchone()[2])
        copied = path.with_name("synthetic-feedback-copy.sqlite")
        with sqlite3.connect(copied) as target:
            original.db.backup(target)
        os.chmod(copied, 0o600)
        feedback = type(original)(str(copied), None)
        self.addCleanup(feedback.db.close)
        self.source = replace(
            self.source, use=replace(self.source.use, tether_store=feedback)
        )
        alternate = self.make()
        with self.assertRaisesRegex(ValueError, "immutable stored identity conflict"):
            alternate.dispatch(self.adapter)
        self.assertEqual(self.http.posts, 0)

    def test_callback_registry_connection_substitution_denied(self) -> None:
        original = self.h.registry.db
        replacement = sqlite3.connect(self.h.path, isolation_level=None)
        self.addCleanup(replacement.close)

        def mutate(request: Any, purpose: str, reference: str) -> bool:
            self.h.registry.db = replacement
            return True

        export = self.make(approve=mutate)
        try:
            with self.assertRaisesRegex(ValueError, "storage binding"):
                export.dispatch(self.adapter)
            self.assertEqual(self.http.posts, 0)
        finally:
            self.h.registry.db = original

    def test_durable_store_connection_substitution_denied(self) -> None:
        store = self.source.store
        original = store._db
        replacement = sqlite3.connect(store.path, isolation_level=None)
        self.addCleanup(replacement.close)

        def mutate(request: Any, purpose: str, reference: str) -> bool:
            store._db = replacement
            return True

        export = self.make(approve=mutate)
        try:
            with self.assertRaisesRegex(ValueError, "storage binding"):
                export.dispatch(self.adapter)
            self.assertEqual(self.http.posts, 0)
        finally:
            store._db = original

    def test_callback_authority_read_transaction_denied(self) -> None:
        for authority in (self.h.registry, self.source.use.tether_store):
            with self.subTest(authority=type(authority).__name__):

                def mutate(request: Any, purpose: str, reference: str) -> bool:
                    if not authority.db.in_transaction:
                        authority.db.execute("BEGIN")
                        authority.db.execute(
                            "SELECT count(*) FROM sqlite_master"
                        ).fetchone()
                    return True

                export = self.make(approve=mutate)
                try:
                    with self.assertRaises((ValueError, UseDenied)):
                        export.dispatch(self.adapter)
                    self.assertEqual(self.http.posts, 0)
                finally:
                    authority.db.rollback()
