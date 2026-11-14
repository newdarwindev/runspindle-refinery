"""Pinned real Tether and Refinery stores; entirely invented local evidence."""

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import storage
from refinery.feedback_collection import Material, SelectedFeedback, collect
from refinery.lineage import GoldRule, OutputRevision, SourceRevision, digest
from refinery.tether_dependency import TetherDependency

ROOT = Path(__file__).resolve().parents[1]
legacy: Any = storage
DEPENDENCY: TetherDependency | None = None


def dependency() -> TetherDependency:
    global DEPENDENCY
    if DEPENDENCY is None:
        path = os.environ.get("RUNSPINDLE_TETHER_SOURCE")
        if not path:
            raise RuntimeError(
                "RUNSPINDLE_TETHER_SOURCE must name prepared exact pinned Tether source; no automatic fetch/fallback"
            )
        DEPENDENCY = TetherDependency(Path(path))
    return DEPENDENCY


class CollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dep = dependency()
        self.a: Any = self.dep.modules["tether.admission"]
        self.j: Any = self.dep.modules["tether.journal"]
        self.f: Any = self.dep.modules["tether.feedback"]
        self.t: Any = self.dep.modules["tether.testing"]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = legacy.Store(self.root / "refinery.sqlite")
        self.addCleanup(self.store.close)
        identity = self.a.Identity("actor", "workspace", "channel", "thread")
        event = dict(
            version="runspindle.tether.ingress.v1",
            event_id="event",
            message_id="input",
            actor_id="actor",
            workspace_id="workspace",
            channel_id="channel",
            thread_id="thread",
            revision=1,
            kind="create",
            text="Synthetic task",
            mirrored=False,
        )
        raw = self.a.canonical(event)
        grant = self.a.Grant(
            identity,
            digest(raw),
            "task",
            1,
            "operation",
            "policy",
            "host",
            1,
            "grant",
            "synthetic",
        )
        self.admission = self.a.Admission(
            str(self.root / "admission.sqlite"),
            lambda raw, proof: identity if proof == digest(raw) else None,
            lambda i, d: grant if i == identity and d == grant.event_digest else None,
        )
        self.addCleanup(self.admission.close)
        self.admission.ingest(raw, digest(raw))
        self.source = SourceRevision(
            "source", "1", "family", b"Synthetic task", digest(b"Synthetic task")
        )
        self.output = OutputRevision(
            "artifact",
            "1",
            "summary",
            "source",
            "1",
            self.source.sha256,
            b"Synthetic answer",
            digest(b"Synthetic answer"),
        )
        self.material = Material(self.source, self.output, "verified-material")
        self.payload = self.t.Payload(
            "Synthetic answer",
            (self.t.Attachment("artifact", "output.txt", self.output.content),),
        )
        self.delivery = self.a.canonical(
            dict(
                version="runspindle.envelope.v1",
                message_type="delivery_request",
                message_id="delivery-event",
                binding=dict(
                    task_id="task",
                    revision=1,
                    operation_id="operation",
                    policy_revision="policy",
                    host_id="host",
                    workspace_id="workspace",
                    lease_epoch=1,
                ),
                payload=dict(
                    artifact_id="artifact",
                    artifact_revision=1,
                    sha256=self.output.sha256,
                    validator_revision="validator",
                    account_id="account",
                    destination_id="destination",
                    delivery_operation_id="delivery",
                ),
            )
        )
        destination = self.t.Destination("workspace", "channel", "thread")
        self.journal = self.j.ProviderJournal(
            str(self.root / "journal.sqlite"),
            self.admission,
            lambda raw, sha: raw == self.delivery and sha == self.payload.digest(),
            lambda a, d: destination,
        )
        self.addCleanup(self.journal.close)
        self.provider = self.t.FakeSlack(
            destination, lambda: 100, lambda: "output-message"
        )
        self.journal.send(self.delivery, self.payload, self.provider)
        self.journal.lookup(self.delivery, self.payload, self.provider)
        self.target = self.journal.retained_target(
            self.delivery, self.payload, "output-message"
        )
        self.feedback = self.f.FeedbackStore(
            str(self.root / "feedback.sqlite"),
            self.journal,
            lambda raw, proof: identity if proof == digest(raw) else None,
            lambda i, e, t: self.f.FeedbackGrant(
                i, e, t, "retention-policy", "retention-grant"
            ),
            lambda raw, p, target: raw == self.delivery
            and p == self.payload
            and target == self.target,
        )
        self.addCleanup(self.feedback.close)
        self.raw = self.event()
        self.feedback.ingest(self.raw, digest(self.raw), self.delivery, self.payload)
        self.selected = SelectedFeedback(
            "synthetic", "feedback", 1, digest(self.raw), "gold"
        )
        self.rules = (GoldRule("summary", "gold", ("positive", "correction")),)
        self.approvals: list[tuple[str, str]] = []
        self.calls_before = len(self.provider.requests)
        self.addCleanup(
            lambda: self.assertEqual(len(self.provider.requests), self.calls_before)
        )
        deny = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = deny.start()
        self.addCleanup(deny.stop)
        self.addCleanup(spy.assert_not_called)

    def event(self, revision: int = 1, **changes: Any) -> bytes:
        event = dict(
            version="runspindle.tether.feedback.v1",
            namespace="synthetic",
            event_id=f"feedback-event-{revision}",
            feedback_id="feedback",
            revision=revision,
            predecessor=None if revision == 1 else revision - 1,
            actor_id="actor",
            workspace_id="workspace",
            channel_id="channel",
            thread_id="thread",
            kind="create" if revision == 1 else "edit",
            judgment="positive",
            correction=None,
            target=self.target,
        )
        event.update(changes)
        return bytes(self.a.canonical(event))

    def approve(self, sha: str, ref: str) -> bool:
        self.approvals.append((sha, ref))
        return True

    def collect(self, **changes: Any) -> str:
        args: dict[str, Any] = dict(
            seed="seed",
            collection_ref="collection-consent",
            training_ref="training-consent",
            resolve_material=lambda target: self.material,
            verify_material=lambda target, material: target == self.target
            and material == self.material,
            approve_collection=self.approve,
            approve_training=self.approve,
        )
        args.update(changes)
        return collect(
            self.store, self.dep, self.feedback, (self.selected,), self.rules, **args
        )

    def test_exact_batch_replay_and_atomic_provenance(self) -> None:
        snapshot = self.collect()
        data, split = self.store.lineage(snapshot)
        self.assertEqual(len(data.examples), 1)
        self.assertEqual(data.examples[0].gold, self.output.content)
        self.assertEqual(data.examples[0].actor_ids, ("actor",))
        self.assertEqual(self.collect(), snapshot)
        batch = self.store.get("lineage", snapshot)["batch_sha256"]
        receipt = self.store.get(
            "feedback_collection",
            self.store.get("lineage", snapshot)["collection_evidence"],
        )
        self.assertEqual(
            receipt["selected"][0]["selection"]["event_sha256"], digest(self.raw)
        )
        self.assertTrue(all(sha == batch for sha, _ in self.approvals))
        self.assertEqual(
            {ref for _, ref in self.approvals},
            {"collection-consent", "training-consent"},
        )
        code = "from refinery.storage import Store; import sys; s=Store(sys.argv[1]); d,p=s.lineage(sys.argv[2]); print(d.examples[0].output.sha256); s.close()"
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.root / "refinery.sqlite"), snapshot],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.output.sha256)

    def test_collection_training_and_source_permission_default_deny(self) -> None:
        for field in (
            "resolve_material",
            "verify_material",
            "approve_collection",
            "approve_training",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.collect(**{field: None})
        for field in ("verify_material", "approve_collection", "approve_training"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.collect(**{field: lambda *args: False})
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_stale_edited_deleted_conflicted_records_are_quarantined(self) -> None:
        edited = self.event(
            2, judgment="correction", correction="Corrected synthetic answer"
        )
        self.feedback.ingest(edited, digest(edited), self.delivery, self.payload)
        snapshot = self.collect()
        data, _ = self.store.lineage(snapshot)
        self.assertEqual(len(data.examples), 0)
        self.assertEqual(data.excluded_counts, {"unavailable_target": 1})
        self.selected = replace(self.selected, revision=2, event_sha256=digest(edited))
        snapshot = self.collect()
        data, _ = self.store.lineage(snapshot)
        self.assertEqual(data.examples[0].gold, b"Corrected synthetic answer")
        deleted = self.event(3, kind="delete", judgment=None)
        self.feedback.ingest(deleted, digest(deleted), self.delivery, self.payload)
        snapshot = self.collect()
        data, _ = self.store.lineage(snapshot)
        self.assertEqual(len(data.examples), 0)
        conflict = self.event(2, judgment="negative")
        self.feedback.ingest(conflict, digest(conflict), self.delivery, self.payload)
        snapshot = self.collect()
        data, _ = self.store.lineage(snapshot)
        self.assertEqual(len(data.examples), 0)

    def test_commit_boundary_revocation_rolls_back_all_source_bytes(self) -> None:
        calls = 0

        def permission(sha: str, ref: str) -> bool:
            nonlocal calls
            calls += 1
            return calls < 3

        with self.assertRaises(ValueError):
            self.collect(approve_training=permission)
        self.assertEqual(calls, 3)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM families").fetchone()[0], 0
        )

    def test_changed_target_or_forged_selection_denied(self) -> None:
        self.material = replace(
            self.material, output=replace(self.output, revision="2")
        )
        with self.assertRaises(ValueError):
            self.collect()
        self.material = Material(self.source, self.output, "verified-material")
        self.selected = replace(self.selected, event_sha256="0" * 64)
        with self.assertRaises(ValueError):
            self.collect()
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_dependency_missing_changed_or_substituted_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TetherDependency(self.root / "missing")
        path = self.dep.root / "tether/feedback.py"
        read = Path.read_bytes

        def altered(p: Path) -> bytes:
            return b"changed" if p == path else read(p)

        with patch.object(Path, "read_bytes", altered), self.assertRaises(ValueError):
            self.collect()
        with (
            patch.dict(sys.modules, {"tether.feedback": object()}),
            self.assertRaises(ValueError),
        ):
            self.collect()
        with self.assertRaises(ValueError):
            collect(
                self.store,
                self.dep,
                object(),
                (self.selected,),
                self.rules,
                seed="seed",
                collection_ref="collection",
                training_ref="training",
                resolve_material=lambda _: self.material,
                verify_material=lambda *_: True,
                approve_collection=lambda *_: True,
                approve_training=lambda *_: True,
            )

    def test_feedback_edit_during_approval_cannot_commit_old_judgment(self) -> None:
        changed = False

        def approve(sha: str, ref: str) -> bool:
            nonlocal changed
            if not changed:
                changed = True
                raw = self.event(2, judgment="negative")
                self.feedback.ingest(raw, digest(raw), self.delivery, self.payload)
            return True

        with self.assertRaisesRegex(ValueError, "feedback changed"):
            self.collect(approve_collection=approve)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_family_rebinding_cannot_move_related_output_between_splits(self) -> None:
        first = self.collect()
        self.material = replace(
            self.material, source=replace(self.source, family_id="different-family")
        )
        with self.assertRaisesRegex(ValueError, "family rebinding"):
            self.collect()
        data, split = self.store.lineage(first)
        self.assertEqual(data.examples[0].source.family_id, "family")
        split.verify(data)

    def test_removed_commit_guard_mutation_exposes_unauthorized_persistence(
        self,
    ) -> None:
        import importlib.util

        source = (ROOT / "refinery/feedback_collection.py").read_text()
        anchor = "            recheck()\n"
        self.assertEqual(source.count(anchor), 1)
        path = self.root / "mutant.py"
        path.write_text(source.replace(anchor, "            pass\n"))
        spec = importlib.util.spec_from_file_location(
            "refinery.mutant_collection", path
        )
        if spec is None or spec.loader is None:
            self.fail("mutation loader unavailable")
        module: Any = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name)
        spec.loader.exec_module(module)
        module.SelectedFeedback = SelectedFeedback
        module.Material = Material
        calls = 0

        def revoked(sha: str, ref: str) -> bool:
            nonlocal calls
            calls += 1
            return calls < 3

        with patch(__name__ + ".collect", module.collect):
            snapshot = self.collect(approve_training=revoked)
        self.assertEqual(calls, 2)
        data, _ = self.store.lineage(snapshot)
        self.assertEqual(
            len(data.examples),
            1,
            "mutant must expose committed data without final consent recheck",
        )

    def test_final_callbacks_cannot_commit_changed_feedback(self) -> None:
        for field in ("verify_material", "approve_collection", "approve_training"):
            for mutation in ("edit", "delete", "conflict"):
                with self.subTest(field=field, mutation=mutation):
                    case = CollectionTests()
                    case.setUp()
                    try:
                        calls = 0
                        final_call = 2 if field == "verify_material" else 3

                        def approve(*args: Any) -> bool:
                            nonlocal calls
                            calls += 1
                            if calls == final_call:
                                if mutation == "delete":
                                    raw = case.event(2, kind="delete", judgment=None)
                                elif mutation == "edit":
                                    raw = case.event(2, judgment="negative")
                                else:
                                    raw = case.event(judgment="negative")
                                case.feedback.ingest(
                                    raw, digest(raw), case.delivery, case.payload
                                )
                            return True

                        with self.assertRaisesRegex(ValueError, "feedback changed"):
                            case.collect(**{field: approve})
                        self.assertEqual(calls, final_call)
                        self.assertEqual(
                            case.store.db.execute(
                                "SELECT count(*) FROM objects"
                            ).fetchone()[0],
                            0,
                        )
                        self.assertEqual(
                            case.store.db.execute(
                                "SELECT count(*) FROM families"
                            ).fetchone()[0],
                            0,
                        )
                    finally:
                        case.doCleanups()
