"""Actual retained source/store and synthetic Langfuse HTTP; no external calls."""

from __future__ import annotations

import copy
import importlib
import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from refinery import feedback_contract as c
from refinery.feedback_projection import ProjectionLedger, RetainedSource
from refinery.feedback_use import RetainedFeedbackUse
from refinery.langfuse_projection import LangfuseProjection, Response


class HTTP:
    def __init__(self) -> None:
        self.origin = "https://langfuse.example"
        self.scores: dict[str, dict[str, Any]] = {}
        self.posts = 0
        self.gets = 0
        self.outage = False
        self.accept_then_fail = False
        self.hidden = False
        self.on_get: Any = lambda: None
        self.on_post: Any = lambda: None

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout: int,
        max_bytes: int,
    ) -> Response:
        if timeout != 10 or max_bytes != 65536:
            raise AssertionError("unbounded transport")
        if self.outage:
            raise TimeoutError("synthetic outage")
        if method == "POST":
            if path != "/api/public/scores" or body is None:
                raise AssertionError("wrong write")
            value = json.loads(body)
            self.posts += 1
            self.scores[value["id"]] = dict(
                id=value["id"],
                projectId="project",
                name=value["name"],
                source=value["source"],
                dataType=value["dataType"],
                value=bool(value["value"]),
                metadata=value["metadata"],
                subject={"kind": "trace", "id": value["traceId"]},
            )
            self.on_post()
            if self.accept_then_fail:
                raise TimeoutError("accepted without acknowledgement")
            return Response(200, c.canonical({"id": value["id"]}))
        query = parse_qs(urlsplit(path).query)
        if urlsplit(path).path != "/api/public/v3/scores" or query["fields"] != [
            "details,subject"
        ]:
            raise AssertionError("wrong read")
        self.gets += 1
        self.on_get()
        row = self.scores.get(query["id"][0])
        return Response(
            200,
            c.canonical(
                {
                    "data": [] if self.hidden or row is None else [row],
                    "meta": {"limit": 2},
                }
            ),
        )


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h = importlib.import_module(
            "test_thumbs_up_collection"
        ).ReactionCollectionTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.selected = self.h.select()
        self.snapshot = self.h.collect(self.selected)
        self.authority = dict(
            revision="policy1",
            collection_allowed=True,
            use_allowed=True,
            export_allowed=True,
            current_target=True,
        )
        self.http = HTTP()
        self.adapter = LangfuseProjection(
            self.http, "project", "trace", self.http.origin
        )
        use = RetainedFeedbackUse(
            self.h.dep,
            self.h.app.store,
            verify_material=lambda target, material: target == self.h.target
            and material == self.h.material,
            approve_use=lambda request: request.purpose
            in {"feedback_projection", "optimization"},
        )

        def grant(key: str, destination: str) -> dict[str, Any]:
            if destination != self.adapter.destination:
                raise ValueError("wrong destination")
            return dict(self.authority)

        self.source = RetainedSource(
            self.h.store, self.snapshot, self.selected, use, grant
        )
        self.path = self.h.root / "projection.sqlite"
        self.ledger = ProjectionLedger(self.path, self.adapter.destination)
        self.addCleanup(self.ledger.close)

    def test_empty_versioned_database_is_not_reinitialized(self) -> None:
        for version in (1, 99):
            path = self.h.root / f"empty-{version}.sqlite"
            db = sqlite3.connect(path)
            db.execute(f"PRAGMA user_version={version}")
            db.close()
            with self.assertRaises(ValueError):
                ProjectionLedger(path, self.adapter.destination)
            db = sqlite3.connect(path)
            try:
                self.assertEqual(
                    db.execute("PRAGMA user_version").fetchone(), (version,)
                )
                self.assertEqual(
                    db.execute("SELECT name FROM sqlite_master").fetchall(), []
                )
            finally:
                db.close()

    def test_incomplete_lineage_never_projects_eligible_gold(self) -> None:
        self.h.store = type(self.h.store)(self.h.root / "incomplete.sqlite")
        self.addCleanup(self.h.store.close)
        self.h.material = replace(
            self.h.material, source=replace(self.h.material.source, complete=False)
        )
        snapshot = self.h.collect(self.selected)
        self.assertEqual(self.h.store.get("lineage", snapshot)["dataset"].examples, ())
        source = replace(self.source, store=self.h.store, snapshot=snapshot)
        value = self.ledger.capture(source)
        self.assertEqual(value["state"], "quarantined")
        self.assertFalse(value["gold"])
        self.assertFalse(value["training_eligible"])

    def test_final_authorizer_source_fence_denied_before_commit(self) -> None:
        mutation = importlib.import_module("test_source_feedback").mutation
        fixture = importlib.import_module("test_thumbs_up_collection")
        NOW, SECRET = fixture.NOW, fixture.SECRET

        from refinery.source_feedback import SourceFeedbackGuard

        module = self.h.dep.modules["tether.source_observation"]
        ingress = self.h.dep.modules["tether.slack_ingress"]
        observer = module.SignedSlackSourceObserver(
            str(self.h.root / "source-fence.sqlite"),
            ingress.SlackMessageVerifier(SECRET, self.h.route, lambda: NOW),
            self.h.route,
            self.h.admission.admission,
            authorize=lambda request: True,
        )
        self.addCleanup(observer.close)
        key = module.SourceKey(
            "ASYNTHETIC", "TSYNTHETIC", "CSYNTHETIC", "1900000000.000002"
        )
        guard = SourceFeedbackGuard(
            self.h.dep,
            observer.store,
            lambda target, material: observer.store.resolve_original(key),
        )
        snapshot = self.h.collect(self.selected, source_guard=guard)
        calls = []

        def authorize(identity: str, destination: str) -> dict[str, Any]:
            calls.append(identity)
            if len(calls) == 2:
                raw = mutation("message_deleted")
                observer.observe(raw, self.h.headers(raw))
            return dict(self.authority)

        source = replace(
            self.source,
            snapshot=snapshot,
            use=replace(self.source.use, source_guard=guard),
            authorize_projection=authorize,
        )
        with self.assertRaisesRegex(ValueError, "source no longer eligible"):
            self.ledger.dispatch(source, self.adapter)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.http.posts, 0)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM projection_revisions"
            ).fetchone()[0],
            0,
        )

    def test_consent_changed_inside_use_callback_denied_before_commit(self) -> None:
        def approve(request: Any) -> bool:
            self.authority["export_allowed"] = False
            return True

        source = replace(self.source, use=replace(self.source.use, approve_use=approve))
        with self.assertRaisesRegex(ValueError, "policy changed"):
            self.ledger.dispatch(source, self.adapter)
        self.assertEqual(self.http.posts, 0)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM projection_revisions"
            ).fetchone()[0],
            0,
        )

    def test_distinct_instances_and_transport_origin_mutation_denied(self) -> None:
        other = HTTP()
        other.origin = "https://other.example"
        adapter = LangfuseProjection(other, "project", "trace", other.origin)
        self.assertNotEqual(adapter.destination, self.adapter.destination)
        with self.assertRaisesRegex(ValueError, "destination"):
            self.ledger.dispatch(self.source, adapter)
        self.http.on_get = lambda: setattr(self.http, "origin", "https://other.example")
        with self.assertRaisesRegex(ValueError, "origin changed"):
            self.ledger.dispatch(self.source, self.adapter)
        self.assertEqual(self.http.posts, 0)

    def test_atomic_capture_delivery_duplicate_and_metadata_only(self) -> None:
        first = self.ledger.capture(self.source)
        self.assertTrue(first["gold"])
        self.assertTrue(first["training_eligible"])
        self.assertEqual(self.ledger.capture(self.source), first)
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "delivered")
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "delivered")
        self.assertEqual(self.http.posts, 1)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM projection_revisions"
            ).fetchone(),
            (1,),
        )
        dump = "".join(self.ledger.db.iterdump())
        for private in (
            "Synthetic answer",
            "USYNTHETIC",
            "Synthetic task",
            "synthetic-refinery-reaction",
        ):
            self.assertNotIn(private, dump)
        self.assertFalse(first["deployment_authority"])

    def test_crash_after_remote_accept_recovers_by_readback_not_repost(self) -> None:
        self.http.accept_then_fail = True
        with self.assertRaises(TimeoutError):
            self.ledger.dispatch(self.source, self.adapter)
        first = self.ledger.capture(self.source)
        self.assertEqual(self.ledger.status(first["projection_id"]), "uncertain")
        recovered = ProjectionLedger(self.path, self.adapter.destination)
        try:
            self.assertEqual(recovered.dispatch(self.source, self.adapter), "delivered")
        finally:
            recovered.close()
        self.assertEqual(self.http.posts, 1)

    def test_outage_before_write_replays_but_ambiguous_absence_never_reposts(
        self,
    ) -> None:
        self.http.outage = True
        with self.assertRaises(TimeoutError):
            self.ledger.dispatch(self.source, self.adapter)
        self.http.outage = False
        self.http.hidden = True
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "uncertain")
        self.assertEqual(self.http.posts, 1)
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "uncertain")
        self.assertEqual(self.http.posts, 1)

    def test_export_withdrawn_during_remote_read_denies_write(self) -> None:
        self.http.on_get = lambda: self.authority.update(
            export_allowed=False, revision="policy2"
        )
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "superseded")
        current = self.ledger.capture(self.source)
        self.assertFalse(current["export_allowed"])
        self.assertEqual(self.http.posts, 0)

    def test_export_withdrawn_during_post_denies_readback_after_reopen(self) -> None:
        first = self.ledger.capture(self.source)
        self.http.on_post = lambda: self.authority.update(
            export_allowed=False, revision="policy2"
        )
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "superseded")
        self.assertIn(first["projection_id"], self.http.scores)
        self.assertEqual((self.http.gets, self.http.posts), (1, 1))
        self.assertEqual(self.ledger.status(first["projection_id"]), "superseded")
        reopened = ProjectionLedger(self.path, self.adapter.destination)
        try:
            self.assertEqual(reopened.dispatch(self.source, self.adapter), "blocked")
        finally:
            reopened.close()
        self.assertEqual((self.http.gets, self.http.posts), (1, 1))

    def test_destination_changed_during_post_denies_readback(self) -> None:
        adapter = self.adapter

        class Transport:
            destination = adapter.destination

            def get(self, projection_id: str) -> dict[str, Any] | None:
                return adapter.get(projection_id)

            def put(self, value: dict[str, Any]) -> None:
                adapter.put(value)
                self.destination = "changed-destination"

        first = self.ledger.capture(self.source)
        with self.assertRaisesRegex(ValueError, "transport destination changed"):
            self.ledger.dispatch(self.source, Transport())
        self.assertEqual((self.http.gets, self.http.posts), (1, 1))
        self.assertEqual(self.ledger.status(first["projection_id"]), "uncertain")

    def test_revocation_during_remote_read_is_sticky_and_negative_revision(
        self,
    ) -> None:
        once = True

        def revoke() -> None:
            nonlocal once
            if once:
                once = False
                raw = self.h.reaction(True)
                self.h.app.react(raw, self.h.headers(raw))

        self.http.on_get = revoke
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "superseded")
        current = self.ledger.capture(self.source)
        self.assertEqual(current["state"], "revoked")
        self.assertFalse(current["gold"])
        self.assertEqual(self.http.posts, 0)
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "delivered")
        self.assertEqual(self.http.posts, 1)

    def test_wrong_destination_project_trace_or_payload_never_settles(self) -> None:
        first = self.ledger.capture(self.source)
        self.adapter.put(first)
        row = self.http.scores[first["projection_id"]]
        for key, value in [
            ("projectId", "other"),
            ("subject", {"kind": "trace", "id": "other"}),
            ("id", "a" * 64),
            ("value", 1),
            ("metadata", {}),
        ]:
            saved = copy.deepcopy(row[key])
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ledger.dispatch(self.source, self.adapter)
            row[key] = saved
        with self.assertRaises(ValueError):
            self.ledger.dispatch(
                self.source,
                LangfuseProjection(self.http, "other", "trace", self.http.origin),
            )

    def test_schema_guard_and_transaction_rollback(self) -> None:
        self.ledger.db.execute(
            "CREATE TRIGGER injected BEFORE INSERT ON projection_outbox BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with self.assertRaisesRegex(ValueError, "schema"):
            self.ledger.capture(self.source)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM projection_revisions"
            ).fetchone(),
            (0,),
        )

    def test_outbox_failure_rolls_back_revision_and_head(self) -> None:
        import sqlite3

        original = self.ledger.db

        class FailOutbox:
            def execute(self, sql: str, *args: Any) -> Any:
                if sql.startswith("INSERT INTO projection_outbox"):
                    raise sqlite3.OperationalError("synthetic outbox write failure")
                return original.execute(sql, *args)

        ledger: Any = self.ledger
        ledger.db = FailOutbox()
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "outbox"):
                ledger.capture(self.source)
        finally:
            ledger.db = original
        for table in ("projection_revisions", "projection_heads", "projection_outbox"):
            self.assertEqual(
                original.execute("SELECT COUNT(*) FROM " + table).fetchone(), (0,)
            )

    def test_stale_dispatcher_cannot_settle_newer_revision(self) -> None:
        first = self.ledger.capture(self.source)
        other = ProjectionLedger(self.path, self.adapter.destination)
        self.addCleanup(other.close)
        once = True

        def newer() -> None:
            nonlocal once
            if once:
                once = False
                self.authority.update(revision="withdrawn", export_allowed=False)
                other.capture(self.source)

        self.http.on_get = newer
        self.assertEqual(self.ledger.dispatch(self.source, self.adapter), "superseded")
        current = other.capture(self.source)
        self.assertGreater(current["revision"], first["revision"])
        self.assertEqual(other.status(current["projection_id"]), "blocked")
        self.assertEqual(self.http.posts, 0)

    def test_removed_fresh_authority_checks_are_detected_by_effect_oracle(self) -> None:
        import inspect
        import textwrap
        from unittest.mock import patch

        module = importlib.import_module("refinery.feedback_projection")
        source = textwrap.dedent(inspect.getsource(ProjectionLedger.dispatch))
        needle = "if self.capture(source) != value:"
        self.assertEqual(source.count(needle), 4)
        namespace = dict(vars(module))
        exec(
            compile(
                source.replace(needle, "if False:"),
                "controlled-authority-mutant",
                "exec",
            ),
            namespace,
        )
        self.http.on_get = lambda: self.authority.update(
            export_allowed=False, revision="policy2"
        )
        with patch.object(ProjectionLedger, "dispatch", namespace["dispatch"]):
            self.ledger.dispatch(self.source, self.adapter)
        # Independent transport counter proves the mutant violated the no-write
        # invariant. This is sensitivity evidence, never acceptable behavior.
        self.assertEqual(self.http.posts, 1)

    def test_delayed_positive_observation_cannot_resurrect_local_tombstone(
        self,
    ) -> None:
        from unittest.mock import patch

        prior = self.source.observe(self.adapter.destination)
        self.ledger.capture(self.source)
        raw = self.h.reaction(True)
        self.h.app.react(raw, self.h.headers(raw))
        revoked = self.ledger.capture(self.source)
        self.assertEqual(revoked["state"], "revoked")
        # Controlled stale-authority result tests the ledger's independent fence,
        # not the real source adapter (which already rejects resurrection).
        with patch.object(RetainedSource, "observe", return_value=prior):
            delayed = self.ledger.capture(self.source)
        self.assertEqual(delayed["state"], "revoked")
        self.assertFalse(delayed["gold"])

    def test_revocation_inside_authority_callback_commits_no_projection(self) -> None:
        from dataclasses import replace

        def revoke(key: str, destination: str) -> dict[str, Any]:
            raw = self.h.reaction(True)
            self.h.app.react(raw, self.h.headers(raw))
            return dict(self.authority)

        source = replace(self.source, authorize_projection=revoke)
        with self.assertRaisesRegex(ValueError, "changed during projection"):
            self.ledger.capture(source)
        self.assertEqual(
            self.ledger.db.execute(
                "SELECT COUNT(*) FROM projection_revisions"
            ).fetchone(),
            (0,),
        )

    def test_copied_json_is_not_source_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "authoritative retained source"):
            self.ledger.capture({"state": "active", "gold": True})  # type: ignore[arg-type]
        self.assertEqual(self.http.posts, 0)


class ContractTests(unittest.TestCase):
    def test_exact_shared_ascii_and_unicode_fixture(self) -> None:
        value = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "contracts/feedback-projection-v1.fixture.json"
            ).read_text()
        )
        for prefix in ("", "unicode_"):
            args = value[prefix + "identity_input"]
            key = c.identity(
                args["namespace"], args["feedback_id"], args["actor_id"], args["target"]
            )
            self.assertEqual(key, value[prefix + "identity_sha256"])
        self.assertEqual(
            c.projection(
                value["identity_sha256"], 1, "c" * 64, value["authority"], "active"
            ),
            value["active"],
        )
        self.assertEqual(
            c.projection(
                value["identity_sha256"], 2, "c" * 64, value["authority"], "revoked"
            ),
            value["revoked"],
        )

    def test_forged_remote_authority_and_bad_digest_rejected(self) -> None:
        value = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "contracts/feedback-projection-v1.fixture.json"
            ).read_text()
        )["active"]
        for field, changed in [
            ("deployment_authority", True),
            ("revision", True),
            ("projection_id", "a" * 64),
        ]:
            bad = dict(value, **{field: changed})
            with self.assertRaises(ValueError):
                c.validate(bad)

    # Contract-only tests above remain separate from retained-source integration.
