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

    def mixed_batches(
        self, *, second_train: bool = False
    ) -> tuple[AuthorizedBatch, AuthorizedBatch]:
        from refinery.feedback_collection import collect
        from refinery.feedback_projection import RetainedSource
        from refinery.feedback_use import RetainedFeedbackUse

        second: Any = importlib.import_module(
            "test_thumbs_up_collection"
        ).ReactionCollectionTests()
        second.actor_id, second.task_text = "USECOND", "Heldout original task"
        second.source_id, second.artifact_id, second.family_id = (
            "source2",
            "artifact2",
            "family2",
        )
        second.event_prefix = "EvSecond"
        second.output_message, second.reaction_time = (
            "1900000000.000005",
            "1900000000.000006",
        )
        second.feedback_path = self.h.h.h.root / "feedback.sqlite"
        second.setUp()
        self.addCleanup(second.doCleanups)
        selected = second.select()
        first = self.h.h.h
        pairs = ((first.target, first.material), (second.target, second.material))

        def verify(target: Any, material: Any) -> bool:
            return any(target == t and material == m for t, m in pairs)

        use = RetainedFeedbackUse(
            first.dep,
            first.app.store,
            verify_material=verify,
            approve_use=lambda _: True,
        )
        for i in range(100) if second_train else (13,):
            snapshot = collect(
                first.store,
                first.dep,
                first.app.store,
                (self.h.h.selected, selected),
                (),
                seed=f"mixed-{i}",
                collection_ref="collection",
                training_ref="training",
                resolve_material=lambda target: next(
                    m for t, m in pairs if t == target
                ),
                verify_material=verify,
                approve_collection=lambda *_: True,
                approve_training=lambda *_: True,
            )
            _, split = first.store.lineage(snapshot)
            if split.partition("family") == "train" and split.partition("family2") == (
                "train" if second_train else "heldout"
            ):
                break
        else:
            self.fail("mixed split unavailable")
        sources = tuple(
            RetainedSource(
                first.store, snapshot, selection, use, self.source.authorize_projection
            )
            for selection in (self.h.h.selected, selected)
        )
        self.source = sources[0]
        batches = []
        for source, purpose in zip(
            sources, ("optimization", "optimization" if second_train else "evaluation")
        ):
            item = self.h.registry.materialize(
                source,
                "synthetic",
                approve=self.h.approve,
                permission_ref="explicit-test-grant",
            )
            selections = (Selection(item["item_id"], source),)
            manifest, _ = capture(
                self.h.registry,
                selections,
                purpose=purpose,
                approve=self.h.approve,
                permission_ref="explicit-test-grant",
            )
            batches.append(
                AuthorizedBatch(
                    manifest,
                    self.h.registry,
                    selections,
                    self.h.approve,
                    "explicit-test-grant",
                )
            )
        return batches[0], batches[1]

    def test_exact_evaluation_batch_under_sealed_owner(self) -> None:
        batches = self.mixed_batches()
        session = self.session(batches=batches)
        compiled = importlib.import_module("test_durable_dspy").compile_fixture(session)
        self.assertEqual(session.claim_evaluation(), compiled)
        session.guard("evaluation")
        with self.assertRaises(ev.Halt):
            session.guard("optimization")
        other = self.session(batches=batches, identity="wrong-heldout-owner")
        with self.assertRaises(ev.Halt):
            other.guard("evaluation")
        self.assertEqual(len(session.provider.calls), 1)
        self.assertEqual(other.provider.calls, [])

    def test_swapped_or_extra_phase_examples_are_denied(self) -> None:
        import json

        batches = self.mixed_batches()
        wrong = json.loads(batches[1].manifest)
        wrong["purpose"] = "optimization"
        from refinery.dataset_contract import canonical

        swapped = replace(batches[1], manifest=canonical(wrong))
        with self.assertRaises(ValueError):
            self.session(batches=(swapped,)).guard("optimization")
        extra = json.loads(batches[0].manifest)
        extra["items"] += json.loads(batches[1].manifest)["items"]
        extra["items"].sort(key=lambda row: row["item"]["item_id"])
        combined = replace(
            batches[0],
            manifest=canonical(extra),
            selections=batches[0].selections + batches[1].selections,
        )
        with self.assertRaises(ValueError):
            self.session(batches=(combined,)).guard("optimization")

    def test_current_callback_withdrawal_is_visible_before_dispatch(self) -> None:
        session = None

        def approve(descriptor: Any, purpose: str, reference: str) -> bool:
            if session is not None:
                self.h.registry.withdraw(
                    self.item["item_id"],
                    approve=self.h.approve,
                    permission_ref="explicit-test-grant",
                )
            return True

        batch = replace(self.batch, approve=approve)
        session = self.session(batches=(batch,))
        with self.assertRaises(ValueError):
            session.claim_compile()
        self.assertIsNone(session.row(session.store.db))
        self.assertEqual(session.provider.calls, [])

    def test_later_phase_item_callback_withdraws_earlier_item(self) -> None:
        batches = self.mixed_batches(second_train=True)
        selections = batches[0].selections + batches[1].selections
        manifest, _ = capture(
            self.h.registry,
            selections,
            purpose="optimization",
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        ids = sorted(s.item_id for s in selections)
        calls = []

        def approve(descriptor: Any, purpose: str, reference: str) -> bool:
            calls.append(descriptor["item_id"])
            if descriptor["item_id"] == ids[1]:
                self.h.registry.withdraw(
                    ids[0], approve=self.h.approve, permission_ref="explicit-test-grant"
                )
            return True

        batch = AuthorizedBatch(
            manifest, self.h.registry, selections, approve, "explicit-test-grant"
        )
        session = self.session(batches=(batch,))
        with self.assertRaises(ValueError):
            session.claim_compile()
        self.assertEqual(calls, ids)
        self.assertIsNone(session.row(session.store.db))
        self.assertEqual(session.provider.calls, [])

    def test_stale_registry_copy_cannot_resume_withdrawn_owner(self) -> None:
        import os

        from refinery.dataset_materialization import DatasetRegistry

        path = self.h.registry.path.with_name("copied-registry.sqlite")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        copy = sqlite3.connect(path)
        self.h.registry.db.backup(copy)
        copy.close()
        registry = DatasetRegistry(path)
        self.addCleanup(registry.close)
        session = self.session()
        session.claim_compile()
        self.h.registry.withdraw(
            self.item["item_id"],
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        copied = replace(self.batch, registry=registry)
        other = self.session(batches=(copied,))
        with self.assertRaisesRegex(ValueError, "immutable DSPy lifecycle conflict"):
            other.guard("optimization")
        self.assertEqual(other.provider.calls, [])

    def test_fresh_process_recovers_exact_owner_without_dispatch(self) -> None:
        import json
        import os
        import subprocess
        import sys
        from pathlib import Path

        session = self.session()
        compiled = importlib.import_module("test_durable_dspy").compile_fixture(session)
        manifest = self.h.registry.path.with_name("batch-manifest.json")
        manifest.write_bytes(self.batch.manifest)
        root = Path(__file__).resolve().parents[1]
        script = r"""
import json, pathlib, socket, sys
sys.path.insert(0, sys.argv[1])
class DenySocket(socket.socket):
    def __init__(self, *a, **k): raise AssertionError("network forbidden")
socket.socket = DenySocket
from refinery.storage import Store, VERSION
from refinery.tether_dependency import TetherDependency
from refinery.feedback_collection import Material, SelectedFeedback
from refinery.feedback_use import RetainedFeedbackUse
from refinery.feedback_projection import RetainedSource
from refinery.dataset_materialization import DatasetRegistry
from refinery.dataset_batch import Selection
from refinery.materialized_use import AuthorizedBatch
from refinery.durable_dspy import Session
from refinery import evaluation as ev
store = Store(pathlib.Path(sys.argv[2]))
dep = TetherDependency(pathlib.Path(sys.argv[3]))
feedback = dep.modules["tether.feedback"].FeedbackStore(sys.argv[4], None)
registry = DatasetRegistry(pathlib.Path(sys.argv[5]))
try:
    snapshot = sys.argv[6]
    value = store.get("lineage", snapshot)
    receipt = store.get("feedback_collection", value["collection_evidence"])
    row = receipt["selected"][0]
    expected = Material(value["batch"][0][0], value["batch"][1][0], row["material_ref"])
    use = RetainedFeedbackUse(dep, feedback,
        verify_material=lambda target, material: target == row["target"] and material == expected,
        approve_use=lambda request: True)
    source = RetainedSource(store, snapshot, SelectedFeedback(**row["selection"]), use,
        lambda key, destination: dict(revision="policy1", collection_allowed=True,
            use_allowed=True, export_allowed=True, current_target=True))
    manifest = pathlib.Path(sys.argv[7]).read_bytes()
    item = json.loads(manifest)["items"][0]["item"]["item_id"]
    batch = AuthorizedBatch(manifest, registry, (Selection(item, source),),
        lambda d, p, r: r == "explicit-test-grant", "explicit-test-grant")
    provider = ev.ScriptedProvider((ev.Completion(b"Synthetic answer",1,1,True,1),), clock=ev.VirtualClock())
    run = Session(store, VERSION, "materialized-run", snapshot, provider=provider,
        limits=ev.Limits(8,100,100,100), per_call=ev.Reservation(10,10),
        budget_id="materialized-budget", retained_use=use, materialized_batches=(batch,))
    result = run.recover_compiled()
    print(json.dumps(dict(candidate=result.candidate_sha256, calls=len(provider.calls),
                         binding=run.binding_sha256, pid=__import__("os").getpid())))
finally:
    registry.close(); feedback.close(); store.close()
"""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                script,
                str(root),
                str(self.source.store.path),
                os.environ["RUNSPINDLE_TETHER_SOURCE"],
                str(
                    self.source.use.tether_store.db.execute(
                        "PRAGMA database_list"
                    ).fetchone()[2]
                ),
                str(self.h.registry.path),
                self.source.snapshot,
                str(manifest),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-4096:])
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["candidate"], compiled.candidate_sha256)
        self.assertEqual(receipt["binding"], session.binding_sha256)
        self.assertEqual(receipt["calls"], 0)
        self.assertNotEqual(receipt["pid"], os.getpid())
