"""Signed synthetic Slack reaction through pinned Tether into exact gold lineage."""

from __future__ import annotations

import hmac
import importlib
import json
import socket
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.feedback_collection import Material, SelectedFeedback, collect
from refinery.feedback_use import RetainedFeedbackUse, UseRequest
from refinery.lineage import THUMBS_UP_GOLD_RULE, OutputRevision, SourceRevision, digest

fixture: Any = importlib.import_module("test_feedback_collection")
storage: Any = importlib.import_module("refinery.storage")

NOW = 1900000000
SECRET = b"synthetic-refinery-reaction-test-not-a-credential"


class ReactionCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dep = fixture.dependency()
        modules = self.dep.modules
        self.a: Any = modules["tether.admission"]
        self.f: Any = modules["tether.feedback"]
        t: Any = modules["tether.testing"]
        j: Any = modules["tether.journal"]
        ingress: Any = modules["tether.slack_ingress"]
        slack: Any = modules["tether.slack_feedback"]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        task_text = getattr(self, "task_text", "Synthetic task")
        source_id = getattr(self, "source_id", "source")
        artifact_id = getattr(self, "artifact_id", "artifact")
        family_id = getattr(self, "family_id", "family")
        self.route = ingress.Route(
            "ASYNTHETIC",
            "TSYNTHETIC",
            getattr(self, "actor_id", "USYNTHETIC"),
            "CSYNTHETIC",
            "1900000000.000001",
        )
        self.admission = ingress.SignedSlackIngress(
            str(self.root / "ingress.sqlite"),
            SECRET,
            self.route,
            lambda: NOW,
            lambda i, d: self.a.Grant(
                i, d, "task", 1, "operation", "policy", "host", 1, "grant", "synthetic"
            ),
        )
        self.addCleanup(self.admission.close)
        raw = self.callback(
            {
                "type": "message",
                "channel": self.route.channel_id,
                "user": self.route.actor_id,
                "text": task_text,
                "ts": "1900000000.000002",
                "thread_ts": self.route.thread_ts,
            },
            "EvTask",
        )
        self.admission.ingest(raw, self.headers(raw))
        self.source = SourceRevision(
            source_id, "1", family_id, task_text.encode(), digest(task_text.encode())
        )
        self.output = OutputRevision(
            artifact_id,
            "1",
            "new-task-type",
            source_id,
            "1",
            self.source.sha256,
            b"Synthetic answer",
            digest(b"Synthetic answer"),
        )
        self.material = Material(self.source, self.output, "verified-current-material")
        self.payload = t.Payload(
            "Synthetic answer",
            (t.Attachment(artifact_id, "answer.txt", self.output.content),),
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
                    workspace_id=self.route.workspace_id,
                    lease_epoch=1,
                ),
                payload=dict(
                    artifact_id=artifact_id,
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
            str(self.root / "journal.sqlite"),
            self.admission.admission,
            lambda raw, sha: raw == self.delivery and sha == self.payload.digest(),
            lambda a, d: destination,
        )
        self.addCleanup(self.journal.close)
        self.message = getattr(self, "output_message", "1900000000.000003")
        provider = t.FakeSlack(destination, lambda: NOW, lambda: self.message)
        self.journal.send(self.delivery, self.payload, provider)
        self.journal.lookup(self.delivery, self.payload, provider)
        target = slack.FeedbackTarget(self.delivery, self.payload, self.message)
        self.app = slack.SignedSlackFeedback(
            str(getattr(self, "feedback_path", self.root / "feedback.sqlite")),
            SECRET,
            self.route,
            lambda: NOW,
            self.journal,
            target,
            authorize_retention=lambda i, e, t: self.f.FeedbackGrant(
                i, e, t, "retention", "grant"
            ),
            verify_target=lambda raw, p, target: raw == self.delivery
            and p == self.payload,
            authorize_invalidation=lambda _: True,
        )
        self.addCleanup(self.app.close)
        self.store = storage.Store(self.root / "refinery.sqlite")
        self.addCleanup(self.store.close)
        self.target = self.journal.retained_target(
            self.delivery, self.payload, self.message
        )
        self.feedback_id = "reaction:" + digest(
            self.a.canonical(
                [self.app.namespace, self.route.actor_id, self.message, "thumbs_up"]
            )
        )
        spy = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        self.addCleanup(spy.stop)
        spy.start()

    def callback(self, event: dict[str, Any], event_id: str) -> bytes:
        return json.dumps(
            dict(
                type="event_callback",
                api_app_id=self.route.app_id,
                team_id=self.route.workspace_id,
                event_id=event_id,
                event=event,
                event_time=NOW,
                is_ext_shared_channel=False,
                authorizations=[
                    dict(
                        team_id=self.route.workspace_id,
                        user_id="UBOT",
                        enterprise_id=None,
                        is_bot=True,
                        is_enterprise_install=False,
                    )
                ],
            )
        ).encode()

    def headers(self, raw: bytes) -> tuple[tuple[str, str], ...]:
        signature = hmac.digest(
            SECRET, b"v0:" + str(NOW).encode() + b":" + raw, "sha256"
        ).hex()
        return (
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(raw))),
            ("X-Slack-Request-Timestamp", str(NOW)),
            ("X-Slack-Signature", "v0=" + signature),
        )

    def reaction(self, removed: bool = False) -> bytes:
        return self.callback(
            dict(
                type="reaction_removed" if removed else "reaction_added",
                user=self.route.actor_id,
                reaction="thumbsup",
                event_ts=getattr(self, "reaction_time", "1900000000.000004"),
                item=dict(
                    type="message", channel=self.route.channel_id, ts=self.message
                ),
            ),
            getattr(self, "event_prefix", "") + ("EvRemove" if removed else "EvAdd"),
        )

    def select(self) -> Any:
        raw = self.reaction()
        self.app.react(raw, self.headers(raw))
        evidence = self.app.store.evidence(self.app.namespace, self.feedback_id)
        row = evidence["revisions"][0]
        return SelectedFeedback(
            self.app.namespace,
            self.feedback_id,
            1,
            row["event_sha256"],
            "bespoke-rule-not-required",
        )

    def collect(self, selected: Any, **changes: Any) -> str:
        args: dict[str, Any] = dict(
            seed="seed",
            collection_ref="collection",
            training_ref="training",
            resolve_material=lambda target: self.material,
            verify_material=lambda target, material: target == self.target
            and material == self.material,
            approve_collection=lambda sha, ref: True,
            approve_training=lambda sha, ref: True,
        )
        args.update(changes)
        return collect(self.store, self.dep, self.app.store, (selected,), (), **args)

    def use(self, snapshot: str, **changes: Any) -> None:
        value = self.store.get("lineage", snapshot)
        args: dict[str, Any] = dict(
            verify_material=lambda target, material: target == self.target
            and material == self.material,
            approve_use=lambda request: True,
        )
        args.update(changes)
        use = RetainedFeedbackUse(self.dep, self.app.store, **args)
        use.verify(
            self.store,
            UseRequest(
                snapshot,
                value["batch_sha256"],
                value["collection_evidence"],
                "optimization",
                "a" * 64,
                "b" * 64,
            ),
        )

    def test_signed_thumb_automatic_gold_duplicate_and_use(self) -> None:
        selected = self.select()
        snapshot = self.collect(selected)
        dataset, _ = self.store.lineage(snapshot)
        self.assertEqual(dataset.examples[0].gold, self.output.content)
        self.assertEqual(dataset.examples[0].rule_id, THUMBS_UP_GOLD_RULE)
        self.assertEqual(dataset.examples[0].feedback[0].kind, "thumbs_up")
        self.assertEqual(self.collect(selected), snapshot)
        self.use(snapshot)

    def test_removal_denies_use_and_recollection_without_resurrection(self) -> None:
        selected = self.select()
        snapshot = self.collect(selected)
        raw = self.reaction(True)
        self.app.react(raw, self.headers(raw))
        with self.assertRaises(ValueError):
            self.use(snapshot)
        data, _ = self.store.lineage(self.collect(selected))
        self.assertFalse(data.examples)
        raw = self.reaction()
        with self.assertRaises(self.a.Rejected):
            self.app.react(raw, self.headers(raw))

    def test_no_collection_training_or_use_consent_is_inferred(self) -> None:
        selected = self.select()
        for field in ("approve_collection", "approve_training"):
            with self.assertRaises(ValueError):
                self.collect(selected, **{field: lambda *args: False})
        snapshot = self.collect(selected)
        with self.assertRaises(ValueError):
            self.use(snapshot, approve_use=lambda request: False)
        with self.assertRaises(ValueError):
            self.use(snapshot, verify_material=lambda *args: False)

    def test_signature_tampering_never_creates_gold(self) -> None:
        raw = self.reaction()
        with self.assertRaises(self.a.Rejected):
            self.app.react(raw + b" ", self.headers(raw))
        self.assertEqual(
            self.app.store.db.execute(
                "SELECT COUNT(*) FROM feedback_revisions"
            ).fetchone()[0],
            0,
        )

    def test_wrong_signed_actor_or_exact_target_is_not_gold(self) -> None:
        base = json.loads(self.reaction())
        for field, value in (
            ("user", "UOTHER"),
            (
                "item",
                {
                    "type": "message",
                    "channel": self.route.channel_id,
                    "ts": "1900000000.999999",
                },
            ),
        ):
            event = dict(base["event"], **{field: value})
            raw = self.callback(event, "EvWrong")
            with self.assertRaises(self.a.Rejected):
                self.app.react(raw, self.headers(raw))
        self.assertEqual(
            self.app.store.db.execute(
                "SELECT COUNT(*) FROM feedback_revisions"
            ).fetchone()[0],
            0,
        )

    def test_supported_plus_one_alias_is_same_attributable_reaction(self) -> None:
        base = json.loads(self.reaction())
        raw = self.callback(dict(base["event"], reaction="+1"), "EvAlias")
        self.assertEqual(self.app.react(raw, self.headers(raw)), "retained")
        self.assertEqual(self.app.react(raw, self.headers(raw)), "duplicate")
        row = self.app.store.evidence(self.app.namespace, self.feedback_id)[
            "revisions"
        ][0]
        selected = SelectedFeedback(
            self.app.namespace, self.feedback_id, 1, row["event_sha256"], "unconfigured"
        )
        dataset, _ = self.store.lineage(self.collect(selected))
        self.assertEqual(len(dataset.examples), 1)
        self.assertEqual(dataset.examples[0].feedback[0].actor_id, self.route.actor_id)

    def invalidate_target(self, cause: str) -> None:
        event: dict[str, Any] = dict(
            type="message",
            channel=self.route.channel_id,
            subtype=cause,
            hidden=True,
            event_ts="1900000000.000005",
        )
        if cause == "message_changed":
            event["message"] = dict(
                type="message",
                ts=self.message,
                user="UBOT",
                bot_id="BSYNTHETIC",
                text="Changed synthetic answer",
                thread_ts=self.route.thread_ts,
            )
        else:
            event["deleted_ts"] = self.message
        raw = self.callback(event, "EvTargetMutation")
        self.app.invalidate_target(raw, self.headers(raw))

    def test_bot_output_edit_revokes_existing_gold_use(self) -> None:
        selected = self.select()
        snapshot = self.collect(selected)
        self.invalidate_target("message_changed")
        with self.assertRaises(ValueError):
            self.use(snapshot)
        data, _ = self.store.lineage(self.collect(selected))
        self.assertFalse(data.examples)
        self.assertEqual(
            self.store.lineage(snapshot)[0].examples[0].gold, self.output.content
        )

    def test_deleted_output_before_reaction_denies_intake(self) -> None:
        self.invalidate_target("message_deleted")
        with self.assertRaises(self.a.Rejected):
            self.select()
        self.assertEqual(
            self.app.store.db.execute(
                "SELECT COUNT(*) FROM feedback_revisions"
            ).fetchone()[0],
            0,
        )

    def test_target_mutation_during_consent_rolls_back_gold(self) -> None:
        selected = self.select()
        changed = False

        def consent(*args: Any) -> bool:
            nonlocal changed
            if not changed:
                changed = True
                self.invalidate_target("message_deleted")
            return True

        with self.assertRaises(ValueError):
            self.collect(selected, approve_training=consent)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM objects").fetchone()[0], 0
        )

    def test_unsupported_skin_tone_is_not_silently_accepted(self) -> None:
        event = json.loads(self.reaction())["event"]
        for reaction in ("thumbsup::skin-tone-3", "+1::skin-tone-3", "👍🏽"):
            raw = self.callback(dict(event, reaction=reaction), "EvUnsupported")
            with self.assertRaises(self.a.Rejected):
                self.app.react(raw, self.headers(raw))
