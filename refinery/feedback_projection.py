"""Authoritative local retained-feedback projections and transactional outbox.

Only this local source bridge decides eligibility; vendor readback never does.
Separate sidecar schema, no changes to immutable lineage or family-split stores.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, cast

from . import feedback_contract as contract
from .feedback_collection import SelectedFeedback
from .feedback_use import RetainedFeedbackUse, UseRequest
from .storage import pack


@dataclass(frozen=True)
class Observation:
    key: str
    provenance: str
    authority: dict[str, Any]
    state: str


@dataclass(frozen=True)
class RetainedSource:
    store: Any
    snapshot: str
    selection: SelectedFeedback
    use: RetainedFeedbackUse
    authorize_projection: Callable[[str, str], dict[str, Any]]

    def observe(self, destination: str) -> Observation:
        contract.text(destination)
        if (
            type(self.selection) is not SelectedFeedback
            or type(self.use) is not RetainedFeedbackUse
        ):
            raise ValueError("explicit retained source required")
        self.use.dependency.check(self.use.tether_store)
        value = self.store.get("lineage", self.snapshot)
        if contract.digest(pack(value)) != self.snapshot:
            raise ValueError("projection snapshot changed")
        self.store.lineage(
            self.snapshot
        )  # Revalidate immutable family-separated dataset.
        receipt = self.store.get("feedback_collection", value["collection_evidence"])
        if (
            contract.digest(pack(receipt)) != value["collection_evidence"]
            or receipt["dependency_commit"] != self.use.dependency.pin["commit"]
        ):
            raise ValueError("projection collection association changed")
        entries = [
            (row, feedback)
            for row, feedback in zip(receipt["selected"], value["batch"][2])
            if row["selection"] == vars(self.selection)
        ]
        if len(entries) != 1:
            raise ValueError("exact retained selection required")
        row, feedback = entries[0]
        original = self.use.tether_store.evidence(
            self.selection.namespace, self.selection.feedback_id
        )
        raw = contract.canonical(original)
        matching = [
            r
            for r in original["revisions"]
            if r["event_sha256"] == self.selection.event_sha256
            and r["event"]["revision"] == self.selection.revision
        ]
        if len(matching) != 1:
            raise ValueError("authenticated original event unavailable")
        event = matching[0]["event"]
        if (
            event["judgment"] != "thumbs_up"
            or event["actor_id"] != feedback.actor_id
            or event["target"] != row["target"]
            or feedback.kind != "thumbs_up"
        ):
            raise ValueError("attributable thumbs-up required")
        key = contract.identity(
            self.selection.namespace,
            self.selection.feedback_id,
            feedback.actor_id,
            event["target"],
        )
        authority = contract.policy(self.authorize_projection(key, destination))
        admitted_policy = dict(authority)
        eligible = any(
            feedback in example.feedback
            and example.output.output_id == feedback.output_id
            and example.output.revision == feedback.output_revision
            and example.output.sha256 == feedback.output_sha256
            and example.gold == example.output.content
            for example in value["dataset"].examples
        )
        state = "active" if eligible else "quarantined"
        if original["state"] in {"invalidated", "deleted"}:
            state = "revoked"
        elif (
            original["state"] != "active"
            or original["head_revision"] != self.selection.revision
        ):
            state = "quarantined"
        request = UseRequest(
            self.snapshot,
            value["batch_sha256"],
            value["collection_evidence"],
            "feedback_projection",
            contract.digest(contract.canonical(authority)),
            key,
        )
        if state == "active":
            try:
                # Caller supplies the original authority. This new purpose must
                # be explicitly allowed; no synthetic/reference fallback.
                self.use.verify(self.store, request)
            except ValueError:
                state = "quarantined"
            if authority["use_allowed"]:
                try:
                    self.use.verify(
                        self.store,
                        UseRequest(
                            self.snapshot,
                            request.batch_sha256,
                            request.collection_receipt_sha256,
                            "optimization",
                            request.configuration_sha256,
                            key,
                        ),
                    )
                except ValueError:
                    authority = dict(authority, use_allowed=False)
        if (
            contract.policy(self.authorize_projection(key, destination))
            != admitted_policy
        ):
            raise ValueError("projection policy changed during authority checks")
        if receipt["version"] == "retained_feedback_collection.v2":
            if self.use.source_guard is None:
                raise ValueError("source use guard unavailable")
            for selected_row in receipt["selected"]:
                observation = selected_row["source_observation"]
                if self.use.source_guard.reread(observation) != observation:
                    raise ValueError("source no longer eligible")
        if (
            contract.canonical(
                self.use.tether_store.evidence(
                    self.selection.namespace, self.selection.feedback_id
                )
            )
            != raw
        ):
            raise ValueError("feedback changed during projection authority checks")
        self.use.dependency.check(self.use.tether_store)
        return Observation(key, contract.digest(raw), authority, state)


class ProjectionTransport(Protocol):
    @property
    def destination(self) -> str: ...
    def get(self, projection_id: str) -> dict[str, Any] | None: ...
    def put(self, value: dict[str, Any]) -> None: ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS projection_configuration(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projection_heads(identity TEXT PRIMARY KEY, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL, tombstone INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS projection_revisions(identity TEXT, revision INTEGER, projection_id TEXT UNIQUE NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(identity,revision));
CREATE TABLE IF NOT EXISTS projection_outbox(projection_id TEXT PRIMARY KEY, destination TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','attempted','uncertain','delivered','superseded','blocked')), attempts INTEGER NOT NULL DEFAULT 0);
CREATE TRIGGER IF NOT EXISTS immutable_projection_update BEFORE UPDATE ON projection_revisions BEGIN SELECT RAISE(ABORT,'immutable projection'); END;
CREATE TRIGGER IF NOT EXISTS immutable_projection_delete BEFORE DELETE ON projection_revisions BEGIN SELECT RAISE(ABORT,'immutable projection'); END;
"""


class ProjectionLedger:
    def __init__(self, path: Path, destination: str) -> None:
        contract.text(destination)
        self.destination = destination
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        try:
            self._initialize(destination)
        except BaseException:
            self.db.close()
            raise

    def _initialize(self, destination: str) -> None:
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA max_page_count=32768")
        existing = self.db.execute("SELECT name FROM sqlite_master").fetchall()
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        fresh = not existing and version == 0
        if not fresh and version != 1:
            raise ValueError("unrecognized projection schema")
        if fresh:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA user_version=1")
        reference = sqlite3.connect(":memory:")
        try:
            reference.executescript(SCHEMA)
            self.expected_schema = tuple(
                reference.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
                )
            )
        finally:
            reference.close()
        self._check_schema()

        self.db.execute(
            "INSERT OR IGNORE INTO projection_configuration VALUES('destination',?)",
            (destination,),
        )
        if self.db.execute(
            "SELECT value FROM projection_configuration WHERE key='destination'"
        ).fetchone() != (destination,):
            raise ValueError("outbox destination changed")

    def _check_schema(self) -> None:
        if (
            self.db.execute("PRAGMA user_version").fetchone() != (1,)
            or tuple(
                self.db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
                )
            )
            != self.expected_schema
        ):
            raise ValueError("projection schema changed")

    def close(self) -> None:
        self.db.close()

    def capture(self, source: RetainedSource) -> dict[str, Any]:
        if type(source) is not RetainedSource:
            raise ValueError("authoritative retained source required")
        self._check_schema()
        observation = source.observe(self.destination)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                "SELECT revision,fingerprint,tombstone FROM projection_heads WHERE identity=?",
                (observation.key,),
            ).fetchone()
            old_value = None
            if old:
                retained = self.db.execute(
                    "SELECT payload FROM projection_revisions WHERE identity=? AND revision=?",
                    (observation.key, old[0]),
                ).fetchone()
                if retained is None:
                    raise ValueError("projection head missing revision")
                old_value = json.loads(retained[0])
                contract.validate(old_value)
                normalized = dict(old_value, revision=1)
                normalized.pop("projection_id")
                normalized["projection_id"] = contract.digest(
                    contract.canonical(normalized)
                )
                maximum = self.db.execute(
                    "SELECT MAX(revision) FROM projection_revisions WHERE identity=?",
                    (observation.key,),
                ).fetchone()[0]
                if (
                    old_value["identity_sha256"] != observation.key
                    or old_value["revision"] != old[0]
                    or maximum != old[0]
                    or old[2] != int(old_value["state"] == "revoked")
                    or old[1] != contract.digest(contract.canonical(normalized))
                ):
                    raise ValueError("projection head association changed")
            state = "revoked" if (old and old[2]) else observation.state
            probe = contract.projection(
                observation.key, 1, observation.provenance, observation.authority, state
            )
            fingerprint = contract.digest(contract.canonical(probe))
            if old and old[1] == fingerprint:
                result = old_value
            else:
                if (
                    self.db.execute(
                        "SELECT COUNT(*) FROM projection_revisions"
                    ).fetchone()[0]
                    >= 100000
                ):
                    raise ValueError("projection ledger full")
                revision = old[0] + 1 if old else 1
                result = contract.projection(
                    observation.key,
                    revision,
                    observation.provenance,
                    observation.authority,
                    state,
                )
                self.db.execute(
                    "INSERT INTO projection_revisions VALUES(?,?,?,?)",
                    (
                        observation.key,
                        revision,
                        result["projection_id"],
                        contract.canonical(result),
                    ),
                )
                self.db.execute(
                    "INSERT OR REPLACE INTO projection_heads VALUES(?,?,?,?)",
                    (observation.key, revision, fingerprint, int(state == "revoked")),
                )
                self.db.execute(
                    "UPDATE projection_outbox SET state='superseded' WHERE projection_id IN (SELECT projection_id FROM projection_revisions WHERE identity=? AND revision<?) AND state!='delivered'",
                    (observation.key, revision),
                )
                self.db.execute(
                    "INSERT INTO projection_outbox(projection_id,destination,state) VALUES(?,?,?)",
                    (
                        result["projection_id"],
                        self.destination,
                        "pending" if result["export_allowed"] else "blocked",
                    ),
                )
            self.db.execute("COMMIT")
            return cast(dict[str, Any], result)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def status(self, projection_id: str) -> str:
        self._check_schema()
        row = self.db.execute(
            "SELECT state FROM projection_outbox WHERE projection_id=?",
            (projection_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown projection")
        return str(row[0])

    def dispatch(self, source: RetainedSource, transport: ProjectionTransport) -> str:
        def require_destination() -> None:
            if transport.destination != self.destination:
                raise ValueError("projection transport destination changed")

        require_destination()
        value = self.capture(source)
        pid = value["projection_id"]

        def readback() -> dict[str, Any] | None:
            require_destination()
            observed = transport.get(pid)
            require_destination()
            return observed

        if not value["export_allowed"]:
            return "blocked"
        if self.status(pid) == "delivered":
            return "delivered"
        # Reads do not imply authority and never establish that an ambiguous
        # async write did not happen. HTTP failure before first write stays pending.
        observed = readback()
        if observed is not None and observed != value:
            raise ValueError("remote projection conflict")
        if self.capture(source) != value:
            return "superseded"
        if observed is None:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                state = self.status(pid)
                if state != "pending":
                    self.db.execute("COMMIT")
                    return "uncertain"
                self.db.execute(
                    "UPDATE projection_outbox SET state='attempted',attempts=attempts+1 WHERE projection_id=? AND state='pending'",
                    (pid,),
                )
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            try:
                if self.capture(source) != value:
                    return "superseded"
                require_destination()
                transport.put(dict(value))
                if self.capture(source) != value:
                    return "superseded"
                observed = readback()
            except BaseException:
                self.db.execute(
                    "UPDATE projection_outbox SET state='uncertain' WHERE projection_id=? AND state='attempted'",
                    (pid,),
                )
                raise
        if self.capture(source) != value:
            return "superseded"
        if observed is None:
            self.db.execute(
                "UPDATE projection_outbox SET state='uncertain' WHERE projection_id=? AND state='attempted'",
                (pid,),
            )
            return "uncertain"
        if observed != value:
            raise ValueError("remote projection conflict")
        # Settle only this exact retained revision. Newer source state cannot be
        # acknowledged by an old claim. No blind re-POST after any attempted write.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            head = self.db.execute(
                "SELECT revision FROM projection_heads WHERE identity=?",
                (value["identity_sha256"],),
            ).fetchone()
            if head != (value["revision"],):
                self.db.execute("COMMIT")
                return "superseded"
            self.db.execute(
                "UPDATE projection_outbox SET state='delivered' WHERE projection_id=?",
                (pid,),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return "delivered"
