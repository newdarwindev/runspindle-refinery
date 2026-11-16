"""Signed source/feedback mutations, actual stores; no live/provider authority."""

import importlib
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import durable_optimization as optimize
from refinery import evaluation as ev
from refinery.feedback_use import RetainedFeedbackUse, UseDenied, UseRequest
from refinery.lineage import digest
from refinery.source_feedback import SourceFeedbackGuard
from refinery.storage import VERSION, pack
from tests.test_signed_feedback_collection import SignedCollectionFixture

signed = importlib.import_module("test_signed_feedback_collection")
ROOT = Path(__file__).resolve().parents[1]
TASK_MESSAGE = "1900000000.000002"


def mutation(
    cause: str, *, feedback: bool = False, channel: str = "CSYNTHETIC"
) -> bytes:
    stamp = signed.MESSAGE if feedback else TASK_MESSAGE
    event: dict[str, Any] = dict(
        type="message", subtype=cause, hidden=True, channel=channel
    )
    if cause == "message_changed":
        event["message"] = dict(
            type="message",
            user="USYNTHETIC",
            ts=stamp,
            thread_ts="1900000000.000001",
            text="changed canary",
        )
    else:
        event["deleted_ts"] = stamp
    return json.dumps(
        dict(
            type="event_callback",
            api_app_id="ASYNTHETIC",
            team_id="TSYNTHETIC",
            event_id="Ev" + cause.replace("_", ""),
            event=event,
        )
    ).encode()


class SourceFixture(SignedCollectionFixture):
    def __init__(self, root: Path, prepare: bool = True) -> None:
        super().__init__(root, prepare)
        module = self.dep.modules["tether.source_observation"]
        verifier = self.dep.modules["tether.slack_ingress"].SlackMessageVerifier(
            signed.SECRET, self.route, lambda: signed.NOW
        )
        self.observe_allowed = True
        self.observer = module.SignedSlackSourceObserver(
            str(root / "sources.sqlite"),
            verifier,
            self.route,
            self.ingress.admission,
            authorize=lambda request: self.observe_allowed,
        )
        self.stack.callback(self.observer.close)
        self.source_key = module.SourceKey(
            "ASYNTHETIC", "TSYNTHETIC", "CSYNTHETIC", TASK_MESSAGE
        )
        self.guard = SourceFeedbackGuard(
            self.dep,
            self.observer.store,
            lambda target, material: self.observer.store.resolve_original(
                self.source_key
            ),
        )
        self.feedback.store.authorize_invalidation = (
            lambda request: self.observe_allowed
        )

    def collect(self, **changes: Any) -> str:
        return super().collect(source_guard=self.guard, **changes)

    def policy(self, **changes: Any) -> RetainedFeedbackUse:
        args: dict[str, Any] = dict(
            verify_material=lambda t, m: t == self.target and m == self.material,
            approve_use=lambda request: self.use,
            source_guard=self.guard,
        )
        args.update(changes)
        return RetainedFeedbackUse(self.dep, self.feedback.store, **args)

    def verify_use(self, snapshot: str, **changes: Any) -> None:
        value = self.store.get("lineage", snapshot)
        self.policy(**changes).verify(
            self.store,
            UseRequest(
                snapshot,
                value["batch_sha256"],
                value["collection_evidence"],
                "evaluation",
                digest(b"config"),
                digest(b"run"),
            ),
        )

    def fence(self, cause: str = "message_deleted", *, feedback: bool = False) -> str:
        raw = mutation(cause, feedback=feedback)
        method = self.feedback.invalidate if feedback else self.observer.observe
        return str(method(raw, signed.headers(raw)))


class SourceFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.h = SourceFixture(self.root)
        self.addCleanup(self.h.close)
        self.calls = list(self.h.provider.requests)
        self.original = tuple(self.h.ingress.admission.db.iterdump())
        self.addCleanup(self.effects)
        no_socket = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        self.sockets = no_socket.start()
        self.addCleanup(no_socket.stop)
        self.addCleanup(self.sockets.assert_not_called)
        self.h.ingest()

    def effects(self) -> None:
        self.assertEqual(self.h.provider.requests, self.calls)
        self.assertEqual(tuple(self.h.ingress.admission.db.iterdump()), self.original)

    def test_source_fence_denies_old_use_preserves_lineage_and_family(self) -> None:
        first = self.h.collect()
        before = pack(self.h.store.get("lineage", first))
        families = list(self.h.store.db.execute("SELECT * FROM families"))
        self.h.verify_use(first)
        self.assertEqual(self.h.fence("message_changed"), "fenced")
        with self.assertRaisesRegex(ValueError, "source no longer eligible"):
            self.h.verify_use(first)
        after = self.h.collect()
        self.assertNotEqual(after, first)
        self.assertEqual(self.h.store.lineage(after)[0].examples, ())
        self.assertEqual(pack(self.h.store.get("lineage", first)), before)
        self.assertEqual(
            list(self.h.store.db.execute("SELECT * FROM families")), families
        )
        self.assertEqual(
            self.h.store.lineage(first)[0].examples[0].actor_ids, ("USYNTHETIC",)
        )
        self.assertEqual(
            len(
                self.h.feedback.store.evidence(
                    self.h.feedback.namespace, signed.MESSAGE
                )["revisions"]
            ),
            1,
        )

    def test_feedback_provider_edit_and_delete_are_negative_only(self) -> None:
        first = self.h.collect()
        self.assertEqual(self.h.fence("message_changed", feedback=True), "invalidated")
        with self.assertRaises(ValueError):
            self.h.verify_use(first)
        self.assertEqual(self.h.fence("message_deleted", feedback=True), "invalidated")
        evidence = self.h.feedback.store.evidence(
            self.h.feedback.namespace, signed.MESSAGE
        )
        self.assertEqual(evidence["state"], "invalidated")
        self.assertEqual(len(evidence["revisions"]), 1)
        self.assertEqual(self.h.store.lineage(self.h.collect())[0].examples, ())
        with self.assertRaises(ValueError):
            self.h.ingest()

    def test_forged_wrong_route_and_missing_authority_do_not_fence(self) -> None:
        first = self.h.collect()
        for feedback in (False, True):
            method = self.h.feedback.invalidate if feedback else self.h.observer.observe
            raw = mutation("message_deleted", feedback=feedback)
            wrong = mutation("message_deleted", feedback=feedback, channel="COTHER")
            for body, proof in (
                (raw, signed.headers(raw, b"wrong")),
                (wrong, signed.headers(wrong)),
            ):
                with self.assertRaises(ValueError):
                    method(body, proof)
            self.h.observe_allowed = False
            with self.assertRaises(ValueError):
                method(raw, signed.headers(raw))
            self.h.observe_allowed = True
        self.h.verify_use(first)
        self.assertEqual(self.h.observer.store.state(self.h.source_key), "unknown")

    def test_unknown_never_replaces_consent_or_material_authority(self) -> None:
        first = self.h.collect()
        for changes in (
            dict(approve_use=lambda request: False),
            dict(verify_material=lambda t, m: False),
            dict(source_guard=None),
        ):
            with self.assertRaises(ValueError):
                self.h.verify_use(first, **changes)
        self.h.training = False
        with self.assertRaises(ValueError):
            self.h.collect()

    def test_wrong_original_task_route_and_unverified_store_fail_closed(self) -> None:
        original = self.h.observer.store.resolve_original(self.h.source_key)
        for bad in (
            replace(original, task_id="other"),
            replace(original, source=replace(original.source, channel_id="COTHER")),
            replace(original, original_intent_sha256="0" * 64),
        ):
            self.h.guard = replace(self.h.guard, resolve_source=lambda t, m: bad)
            with self.assertRaises(ValueError):
                self.h.collect()
        self.h.guard = replace(self.h.guard, observation_store=object())
        with self.assertRaises(ValueError):
            self.h.collect()
        self.assertEqual(
            self.h.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_fence_in_approval_callback_rolls_back_collection(self) -> None:
        def consent(sha: str, ref: str) -> bool:
            self.h.fence()
            return True

        with self.assertRaisesRegex(ValueError, "source changed during collection"):
            self.h.collect(approve_training=consent)
        self.assertEqual(
            self.h.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_fence_in_use_callback_denies_before_training_calls(self) -> None:
        first = self.h.collect(seed="train-source")

        def approve(request: Any) -> bool:
            self.h.fence()
            return True

        provider = ev.ScriptedProvider(
            (ev.Completion(self.h.output.content, 1, 1, True, 1),) * 4,
            clock=ev.VirtualClock(),
        )
        with self.assertRaises(UseDenied):
            optimize.run(
                self.h.store,
                VERSION,
                "run",
                first,
                (b"candidate", b"other"),
                provider=provider,
                limits=ev.Limits(20, 200, 2000, 200),
                per_call=ev.Reservation(10, 10),
                budget_id="budget",
                retained_use=self.h.policy(approve_use=approve),
            )
        self.assertEqual(provider.calls, [])
        self.assertEqual(
            self.h.store.db.execute("SELECT count(*) FROM experiments").fetchone()[0], 0
        )

    def test_fresh_process_replay_keeps_fence_and_denies_use(self) -> None:
        first = self.h.collect()
        self.h.fence()
        script = """
import sys, socket
from pathlib import Path
from unittest.mock import patch

from tests.test_signed_feedback_collection import SignedCollectionFixture
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_source_feedback import SourceFixture
h = SourceFixture(Path(sys.argv[1]), False)
try:
 with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')) as sockets:
  if h.fence() != 'duplicate': raise RuntimeError('replay')
  try: h.verify_use(sys.argv[2])
  except ValueError: pass
  else: raise RuntimeError('fenced use')
  snapshot = h.collect()
  if h.store.lineage(snapshot)[0].examples: raise RuntimeError('fenced import')
  if len(h.store.lineage(sys.argv[2])[0].examples) != 1: raise RuntimeError('history rewritten')
  if h.provider.requests: raise RuntimeError('provider effect')
  sockets.assert_not_called()
finally: h.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", script, str(self.root), first],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_feedback_invalidation_survives_fresh_process_without_source_fence(
        self,
    ) -> None:
        first = self.h.collect()
        self.h.fence(feedback=True)
        script = """
import sys, socket
from pathlib import Path
from unittest.mock import patch

from tests.test_signed_feedback_collection import SignedCollectionFixture
network = patch.object(socket, 'socket', side_effect=AssertionError('network forbidden'))
sockets = network.start()
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_source_feedback import SourceFixture
h = SourceFixture(Path(sys.argv[1]), False)
try:
 if h.observer.store.state(h.source_key) != 'unknown': raise RuntimeError('wrong ledger')
 if h.fence(feedback=True) != 'duplicate': raise RuntimeError('feedback replay')
 try: h.verify_use(sys.argv[2])
 except ValueError: pass
 else: raise RuntimeError('invalidated use')
 if h.store.lineage(h.collect())[0].examples: raise RuntimeError('invalidated import')
 if h.provider.requests: raise RuntimeError('provider effect')
finally:
 sockets.assert_not_called()
 network.stop()
 h.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", script, str(self.root), first],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_resolver_feedback_mutation_after_initial_read_denies(self) -> None:
        first = self.h.collect()
        resolve = self.h.guard.resolve_source
        calls = 0

        def resolver(target: Any, material: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.h.fence(feedback=True)
            return resolve(target, material)

        guard = replace(self.h.guard, resolve_source=resolver)
        with self.assertRaisesRegex(ValueError, "feedback changed"):
            self.h.verify_use(first, source_guard=guard)

    def test_collection_resolver_mutation_does_not_commit_stale_feedback(self) -> None:
        resolve = self.h.guard.resolve_source
        calls = 0

        def resolver(target: Any, material: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 3:
                self.h.fence(feedback=True)
            return resolve(target, material)

        self.h.guard = replace(self.h.guard, resolve_source=resolver)
        with self.assertRaisesRegex(ValueError, "feedback changed"):
            self.h.collect()
        self.assertEqual(
            self.h.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )

    def test_known_source_binding_cannot_be_downgraded_to_legacy_receipt(self) -> None:
        # Historical v1 remains readable but cannot execute once this identity
        # has been collected with a source binding in the same component store.
        legacy = signed.SignedCollectionFixture.collect(self.h)
        first = self.h.collect()
        self.h.fence()
        with self.assertRaisesRegex(ValueError, "requires source guard"):
            signed.SignedCollectionFixture.collect(self.h)
        with self.assertRaisesRegex(ValueError, "requires source guard"):
            signed.SignedCollectionFixture.verify_use(self.h, legacy)
        self.assertEqual(len(self.h.store.lineage(first)[0].examples), 1)

    def test_mutated_state_reader_is_detected_by_independent_negative(self) -> None:
        # Controlled fault: remove only the source fence observation. The same
        # regression must fail, rather than trusting a self-reported blocked label.
        module = self.h.dep.modules["tether.source_observation"]
        with patch.object(
            module.SourceObservationStore, "state", return_value="unknown"
        ):
            result = unittest.TestResult()
            SourceFeedbackTests(
                "test_source_fence_denies_old_use_preserves_lineage_and_family"
            ).run(result)
        self.assertEqual(result.errors, [], repr(result.errors))
        self.assertTrue(result.failures, "removed fence escaped regression")
