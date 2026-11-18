"""Verified signed Slack creates into real Tether and Refinery SQLite stores.

No live Slack, mutable-event freshness, provider/training execution or production
source/consent authority. Existing component APIs are consumed, not reimplemented.
"""

import hmac
import importlib
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import tether_dependency
from refinery.feedback_collection import Material, SelectedFeedback, collect
from refinery.feedback_use import RetainedFeedbackUse, UseRequest
from refinery.lineage import GoldRule, OutputRevision, SourceRevision, digest
from refinery.storage import Store

fixtures = importlib.import_module("test_feedback_collection")
ROOT = Path(__file__).resolve().parents[1]
SECRET = b"synthetic-slack-signing-key-no-account"
NOW = 1900000000
MESSAGE = "1900000000.000003"


def signed_message(text: str, *, task: bool = False, **changes: Any) -> bytes:
    event = dict(
        type="message",
        user="USYNTHETIC",
        channel="CSYNTHETIC",
        thread_ts="1900000000.000001",
        ts="1900000000.000002" if task else MESSAGE,
        text=text,
    )
    event.update(changes)
    return json.dumps(
        dict(
            type="event_callback",
            api_app_id="ASYNTHETIC",
            team_id="TSYNTHETIC",
            event_id="EvTASK" if task else "EvFEEDBACK",
            event=event,
        ),
        indent=2,
    ).encode()


def headers(raw: bytes, secret: bytes = SECRET) -> tuple[tuple[str, str], ...]:
    # Independent fixture signer; product uses hmac.new and verifies raw bytes.
    signature = hmac.digest(secret, f"v0:{NOW}:".encode() + raw, "sha256").hex()
    return (
        ("Content-Type", "application/json"),
        ("X-Slack-Request-Timestamp", str(NOW)),
        ("X-Slack-Signature", "v0=" + signature),
    )


class SignedCollectionFixture:
    def __init__(self, root: Path, prepare: bool = True) -> None:
        self.stack = ExitStack()
        self.dep = fixtures.dependency()
        m = self.dep.modules
        a, j, f = m["tether.admission"], m["tether.journal"], m["tether.feedback"]
        t, si, sf = (
            m["tether.testing"],
            m["tether.slack_ingress"],
            m["tether.slack_feedback"],
        )
        self.route = si.Route(
            "ASYNTHETIC", "TSYNTHETIC", "USYNTHETIC", "CSYNTHETIC", "1900000000.000001"
        )
        self.ingress = si.SignedSlackIngress(
            str(root / "admission.sqlite"),
            SECRET,
            self.route,
            lambda: NOW,
            lambda i, d: a.Grant(
                i, d, "task", 1, "operation", "policy", "host", 1, "grant", "synthetic"
            )
            if i == self.route.identity
            else None,
        )
        self.stack.callback(self.ingress.close)
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
        self.material = Material(self.source, self.output, "synthetic-source-verifier")
        self.payload = t.Payload(
            "Synthetic answer",
            (t.Attachment("artifact", "output.txt", self.output.content),),
        )
        self.delivery = a.canonical(
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
                    workspace_id=self.route.workspace_id,
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
        destination = t.Destination(
            self.route.workspace_id, self.route.channel_id, self.route.thread_ts
        )
        self.journal = j.ProviderJournal(
            str(root / "journal.sqlite"),
            self.ingress.admission,
            lambda raw, sha: raw == self.delivery and sha == self.payload.digest(),
            lambda a, d: destination,
        )
        self.stack.callback(self.journal.close)
        self.provider = t.FakeSlack(
            destination, lambda: NOW, lambda: "1900000000.000002"
        )
        if prepare:
            task = signed_message("Synthetic task", task=True)
            self.ingress.ingest(task, headers(task))
            self.journal.send(self.delivery, self.payload, self.provider)
            self.journal.lookup(self.delivery, self.payload, self.provider)
        self.target = self.journal.retained_target(
            self.delivery, self.payload, "1900000000.000002"
        )
        self.retention = True
        self.collection = True
        self.training = True
        self.use = True
        self.feedback = sf.SignedSlackFeedback(
            str(root / "feedback.sqlite"),
            SECRET,
            self.route,
            lambda: NOW,
            self.journal,
            sf.FeedbackTarget(self.delivery, self.payload, "1900000000.000002"),
            lambda i, e, target: f.FeedbackGrant(
                i, e, target, "synthetic-retention-policy", "synthetic-retention-grant"
            )
            if self.retention
            else None,
            lambda raw, p, target: raw == self.delivery
            and p == self.payload
            and target == self.target,
        )
        self.stack.callback(self.feedback.close)
        store_type: Any = Store
        self.store: Any = store_type(root / "refinery.sqlite")
        self.stack.callback(self.store.close)
        self.rules = (GoldRule("summary", "gold", ("positive", "correction")),)

    def close(self) -> None:
        self.stack.close()

    def ingest(self, raw: bytes | None = None, proof: Any = None) -> str:
        raw = signed_message("feedback positive") if raw is None else raw
        return str(self.feedback.ingest(raw, headers(raw) if proof is None else proof))

    def selection(self) -> SelectedFeedback:
        evidence = self.feedback.store.evidence(self.feedback.namespace, MESSAGE)
        return SelectedFeedback(
            self.feedback.namespace,
            MESSAGE,
            1,
            evidence["revisions"][0]["event_sha256"],
            "gold",
        )

    def collect(self, **changes: Any) -> str:
        args: dict[str, Any] = dict(
            seed="seed",
            collection_ref="synthetic-collection-consent",
            training_ref="synthetic-training-consent",
            resolve_material=lambda target: self.material,
            verify_material=lambda target, material: target == self.target
            and material == self.material,
            approve_collection=lambda sha, ref: self.collection,
            approve_training=lambda sha, ref: self.training,
        )
        args.update(changes)
        return collect(
            self.store,
            self.dep,
            self.feedback.store,
            (self.selection(),),
            self.rules,
            **args,
        )

    def verify_use(self, snapshot: str) -> None:
        value = self.store.get("lineage", snapshot)
        request = UseRequest(
            snapshot,
            value["batch_sha256"],
            value["collection_evidence"],
            "evaluation",
            digest(b"synthetic-config"),
            digest(b"synthetic-run"),
        )
        policy = RetainedFeedbackUse(
            self.dep,
            self.feedback.store,
            verify_material=lambda target, material: target == self.target
            and material == self.material,
            approve_use=lambda request: self.use,
        )
        policy.verify(self.store, request)


class SignedCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.h = SignedCollectionFixture(self.root)
        self.addCleanup(self.h.close)
        self.calls = list(self.h.provider.requests)
        self.admitted = self.h.ingress.admission.db.total_changes
        self.addCleanup(self.effects)
        denied = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        observed = denied.start()
        self.addCleanup(denied.stop)
        self.addCleanup(observed.assert_not_called)

    def effects(self) -> None:
        self.assertEqual(self.h.provider.requests, self.calls)
        self.assertEqual(self.h.ingress.admission.db.total_changes, self.admitted)

    def test_signed_attribution_exact_revision_and_idempotent_import(self) -> None:
        self.assertEqual(self.h.ingest(), "retained")
        first = self.h.collect()
        self.assertEqual(self.h.ingest(), "duplicate")
        self.assertEqual(self.h.collect(), first)
        data, split = self.h.store.lineage(first)
        self.assertEqual(data.examples[0].actor_ids, ("USYNTHETIC",))
        self.assertEqual(data.examples[0].output, self.h.output)
        self.assertEqual(data.examples[0].gold, b"Synthetic answer")
        split.verify(data)
        self.h.verify_use(first)
        value = self.h.store.get("lineage", first)
        receipt = self.h.store.get("feedback_collection", value["collection_evidence"])
        self.assertEqual(
            receipt["dependency_commit"], "1e8d2091c55dfad47fdc64ec2e73fcd946da9921"
        )
        self.assertEqual(
            receipt["selected"][0]["selection"]["event_sha256"],
            self.h.selection().event_sha256,
        )

    def child(self, action: str, prior_snapshot: str = "") -> dict[str, Any]:
        code = """
import json, socket, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_signed_feedback_collection import SignedCollectionFixture, signed_message
h = SignedCollectionFixture(Path(sys.argv[1]), False)
try:
    with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')) as sockets:
        before = h.ingress.admission.db.total_changes
        if sys.argv[2] == 'revoke':
            h.retention = False
            try: h.ingest()
            except ValueError: pass
            else: raise RuntimeError('revoked retention replay admitted')
            h.collection = False
            try: h.collect()
            except ValueError: pass
            else: raise RuntimeError('revoked collection admitted')
            h.use = False
            try: h.verify_use(sys.argv[3])
            except ValueError: pass
            else: raise RuntimeError('revoked use admitted')
            status, snapshot = 'revoked', sys.argv[3]
        else:
            status = h.ingest(signed_message('feedback negative')) if sys.argv[2] == 'conflict' else h.ingest()
            snapshot = h.collect()
        data, split = h.store.lineage(snapshot)
        sockets.assert_not_called()
        if h.provider.requests or h.ingress.admission.db.total_changes != before:
            raise RuntimeError('unexpected execution effect')
        print(json.dumps(dict(status=status, snapshot=snapshot, rows=len(data.examples), excluded=data.excluded_counts)))
finally:
    h.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.root), action, prior_snapshot],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(json.loads(result.stdout))

    def test_fresh_process_import_replay_and_conflict_quarantine(self) -> None:
        self.h.ingest()
        first = self.h.collect()
        replay = self.child("replay")
        self.assertEqual(
            replay, dict(status="duplicate", snapshot=first, rows=1, excluded={})
        )
        revoked = self.child("revoke", first)
        self.assertEqual(
            revoked, dict(status="revoked", snapshot=first, rows=1, excluded={})
        )
        conflict = self.child("conflict")
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["rows"], 0)
        self.assertEqual(conflict["excluded"], {"conflicting_feedback_id": 1})
        self.assertNotEqual(conflict["snapshot"], first)
        self.assertEqual(len(self.h.store.lineage(first)[0].examples), 1)
        with self.assertRaisesRegex(ValueError, "no longer current"):
            self.h.verify_use(first)

    def test_forged_provider_proof_cannot_create_dataset(self) -> None:
        raw = signed_message("feedback positive")
        forged = signed_message("feedback negative")
        for body, proof in (
            (forged, headers(raw)),
            (raw, headers(raw, b"synthetic-wrong-key")),
            (
                signed_message("feedback positive", user="UOTHER"),
                headers(signed_message("feedback positive", user="UOTHER")),
            ),
        ):
            with self.assertRaises(ValueError):
                self.h.ingest(body, proof)
        with self.assertRaises(ValueError):
            self.h.collect()
        self.assertEqual(
            self.h.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_retention_and_collection_use_revocations_remain_separate(self) -> None:
        self.h.retention = False
        with self.assertRaises(ValueError):
            self.h.ingest()
        with self.assertRaises(ValueError):
            self.h.collect()
        self.h.retention = True
        self.h.ingest()
        snapshot = self.h.collect()
        self.h.retention = False
        with self.assertRaises(ValueError):
            self.h.ingest()  # even exact transport replay rechecks retention
        self.h.collection = False
        with self.assertRaisesRegex(ValueError, "collection"):
            self.h.collect()
        self.h.use = False
        with self.assertRaisesRegex(ValueError, "use authority denied"):
            self.h.verify_use(snapshot)
        self.assertEqual(len(self.h.store.lineage(snapshot)[0].examples), 1)

    def test_exact_output_family_and_dual_consent_cannot_be_rebound(self) -> None:
        self.h.ingest()
        snapshot = self.h.collect()
        original = self.h.material
        self.h.material = replace(
            original, output=replace(original.output, revision="2")
        )
        with self.assertRaisesRegex(ValueError, "exact source/output revision"):
            self.h.collect()
        self.h.material = replace(
            original, source=replace(original.source, family_id="other-family")
        )
        with self.assertRaisesRegex(ValueError, "family rebinding"):
            self.h.collect()
        self.h.material = original
        self.h.training = False
        with self.assertRaisesRegex(ValueError, "training"):
            self.h.collect()
        self.h.store.lineage(snapshot)[1].verify(self.h.store.lineage(snapshot)[0])

    def test_provider_edits_deletes_not_claimed_as_freshness(self) -> None:
        self.h.ingest()
        for subtype in ("message_changed", "message_deleted"):
            with self.assertRaises(ValueError):
                self.h.ingest(signed_message("feedback negative", subtype=subtype))
        # The immutable historical create survives: rejecting mutations is not
        # evidence that the provider message is still current.
        self.assertEqual(
            self.h.feedback.store.evidence(self.h.feedback.namespace, MESSAGE)["state"],
            "active",
        )

    def test_only_pin_git_inspection_subprocesses_during_collection(self) -> None:
        self.h.ingest()
        original = subprocess.Popen
        observed = []

        def limited(args: Any, *positional: Any, **options: Any) -> Any:
            if args != [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(self.h.dep.root),
                "rev-parse",
                "HEAD",
            ]:
                raise AssertionError("unexpected execution")
            observed.append(args)
            return original(args, *positional, **options)

        with patch.object(subprocess, "Popen", side_effect=limited):
            snapshot = self.h.collect()
            self.h.verify_use(snapshot)
        self.assertGreater(len(observed), 0)

    def test_verified_loader_rejects_modified_signed_adapter(self) -> None:
        self.h.ingest()
        original = tether_dependency.bounded_file
        adapter = self.h.dep.root / "tether/slack_feedback.py"

        def altered(path: Path) -> bytes:
            return b"modified adapter" if path == adapter else original(path)

        with (
            patch.object(tether_dependency, "bounded_file", altered),
            self.assertRaisesRegex(ValueError, "source changed"),
        ):
            self.h.collect()
        self.assertEqual(
            self.h.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_bypassed_signature_comparison_mutation_fails_real_regression(self) -> None:
        # Deliberate primitive fault removes raw-body authentication while keeping
        # real signed-adapter/store/collector execution. The independent forgery
        # regression must fail. Independently observe a contaminated dataset
        # under the same fault, rather than infer persistence from a failure.
        with patch.object(hmac, "compare_digest", return_value=True):
            raw = signed_message("feedback positive")
            self.assertEqual(
                self.h.ingest(raw, headers(raw, b"synthetic-wrong-key")), "retained"
            )
            contaminated = self.h.collect()
            self.assertEqual(len(self.h.store.lineage(contaminated)[0].examples), 1)
            result = unittest.TestResult()
            SignedCollectionTests(
                "test_forged_provider_proof_cannot_create_dataset"
            ).run(result)
        self.assertEqual(result.errors, [], repr(result.errors))
        self.assertTrue(
            result.failures, "authentication bypass escaped pipeline regression"
        )
