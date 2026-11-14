"""Bounded component-owned SQLite storage. No collection or network transport.

SQLite transactions qualify process interruption, not hostile storage/power loss.
The codec is explicit, deterministic JSON: never pickle or executable payloads.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import sqlite3
from typing import Protocol

from . import lineage as lin
from . import evaluation as ev
from .optimization import Selection

VERSION = "runspindle.maintained-evidence.v1"
MAX_PAYLOAD = 1_048_576
MAX_TOTAL = 67_108_864
MAX_OBJECTS = 1000
TYPES = {
    t.__name__: t
    for t in (
        lin.SourceRevision,
        lin.OutputRevision,
        lin.Feedback,
        lin.GoldRule,
        lin.Example,
        lin.Quarantine,
        lin.Dataset,
        lin.LockedSplit,
        ev.Limits,
        ev.Reservation,
        ev.Configuration,
        ev.Experiment,
        ev.Request,
        ev.Attempt,
        ev.Report,
        ev.Metrics,
        Selection,
    )
}


def version(value):
    if value != VERSION:
        raise ValueError("unsupported maintained evidence version")


def _encode(value):
    if is_dataclass(value) and type(value).__name__ in TYPES:
        return {
            "type": type(value).__name__,
            "fields": {f.name: _encode(getattr(value, f.name)) for f in fields(value)},
        }
    if type(value) is bytes:
        return {"bytes": base64.b64encode(value).decode("ascii")}
    if type(value) is tuple:
        return {"tuple": [_encode(x) for x in value]}
    if type(value) is dict and all(type(k) is str for k in value):
        return {"dict": {k: _encode(v) for k, v in value.items()}}
    if value is None or type(value) in (str, int, bool, float):
        return value
    raise ValueError("unsupported stored type")


def _decode(value, depth=0):
    if depth > 32:
        raise ValueError("stored nesting limit")
    if type(value) is not dict:
        if value is None or type(value) in (str, int, bool, float):
            return value
        raise ValueError("invalid stored value")

    def walk(x):
        return _decode(x, depth + 1)

    if set(value) == {"bytes"}:
        return base64.b64decode(value["bytes"], validate=True)
    if set(value) == {"tuple"}:
        return tuple(walk(x) for x in value["tuple"])
    if set(value) == {"dict"}:
        return {k: walk(v) for k, v in value["dict"].items()}
    if set(value) == {"type", "fields"} and value["type"] in TYPES:
        cls = TYPES[value["type"]]
        if set(value["fields"]) != {f.name for f in fields(cls)}:
            raise ValueError("stored schema mismatch")
        return cls(**{k: walk(v) for k, v in value["fields"].items()})
    raise ValueError("unsupported stored schema")


def pack(value):
    data = json.dumps(
        _encode(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(data) > MAX_PAYLOAD:
        raise ValueError("stored payload limit")
    return data


def unpack(data):
    if type(data) is not bytes or len(data) > MAX_PAYLOAD:
        raise ValueError("stored payload limit")
    value = _decode(json.loads(data))
    if pack(value) != data:
        raise ValueError("noncanonical stored record")
    return value


class LineageStorage(Protocol):
    def commit_lineage(
        self,
        api_version,
        sources,
        outputs,
        feedback,
        rules,
        *,
        seed,
        collection_evidence,
        verify_collection=None,
        verify_actor=None,
    ): ...
    def lineage(self, snapshot_sha256): ...


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA max_page_count=32768")
            with self.transaction() as tx:
                schema = tx.execute("PRAGMA user_version").fetchone()[0]
                if schema not in (0, 1):
                    raise ValueError("unsupported refinery store schema")
                if schema == 0:
                    if tx.execute(
                        "SELECT count(*) FROM sqlite_master WHERE type='table'"
                    ).fetchone()[0]:
                        raise ValueError("unversioned nonempty store")
                    statements = [
                        "CREATE TABLE objects(kind TEXT, identity TEXT, digest TEXT NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(kind,identity))",
                        "CREATE TABLE families(identity TEXT PRIMARY KEY, family TEXT NOT NULL)",
                        "CREATE TABLE budgets(identity TEXT PRIMARY KEY, payload BLOB NOT NULL)",
                        "CREATE TABLE experiments(identity TEXT PRIMARY KEY, binding BLOB NOT NULL, state TEXT NOT NULL, report TEXT)",
                        "CREATE TABLE attempts(experiment TEXT, ticket INTEGER, request BLOB NOT NULL, outcome TEXT NOT NULL, receipt BLOB, PRIMARY KEY(experiment,ticket))",
                        "CREATE TABLE environments(identity TEXT PRIMARY KEY, generation INTEGER NOT NULL, active TEXT, pending TEXT)",
                        "CREATE TABLE activations(identity TEXT PRIMARY KEY, environment TEXT NOT NULL, generation INTEGER NOT NULL, candidate TEXT NOT NULL, predecessor TEXT, state TEXT NOT NULL, approval TEXT NOT NULL, UNIQUE(environment,generation))",
                    ]
                    for statement in statements:
                        tx.execute(statement)
                    for table in ("objects", "families"):
                        for action in ("UPDATE", "DELETE"):
                            tx.execute(
                                f"CREATE TRIGGER {table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable retained record'); END"
                            )
                    tx.execute("PRAGMA user_version=1")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @staticmethod
    def room(tx, table, limit=MAX_OBJECTS):
        if table not in {
            "objects",
            "families",
            "budgets",
            "experiments",
            "attempts",
            "environments",
            "activations",
        }:
            raise ValueError("unknown bounded table")
        if tx.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= limit:
            raise ValueError("store capacity")

    @staticmethod
    def put(tx, kind, identity, value):
        lin._token(kind)
        if type(identity) is not str or not 1 <= len(identity) <= 4096:
            raise ValueError("invalid stored identity")
        data = pack(value)
        sha = lin.digest(data)
        row = tx.execute(
            "SELECT digest,payload FROM objects WHERE kind=? AND identity=?",
            (kind, identity),
        ).fetchone()
        if row:
            if row != (sha, data):
                raise ValueError("immutable stored identity conflict")
            return sha
        if (
            tx.execute("SELECT count(*) FROM objects WHERE kind=?", (kind,)).fetchone()[
                0
            ]
            >= MAX_OBJECTS
        ):
            raise ValueError("record capacity")
        if (
            tx.execute(
                "SELECT coalesce(sum(length(payload)),0) FROM objects"
            ).fetchone()[0]
            + len(data)
            > MAX_TOTAL
        ):
            raise ValueError("total payload capacity")
        tx.execute("INSERT INTO objects VALUES(?,?,?,?)", (kind, identity, sha, data))
        return sha

    def get(self, kind, identity):
        row = self.db.execute(
            "SELECT digest,payload FROM objects WHERE kind=? AND identity=?",
            (kind, identity),
        ).fetchone()
        if not row or lin.digest(row[1]) != row[0]:
            raise ValueError("unavailable or corrupt immutable record")
        return unpack(row[1])

    def commit_lineage(
        self,
        api_version,
        sources,
        outputs,
        feedback,
        rules,
        *,
        seed,
        collection_evidence,
        verify_collection=None,
        verify_actor=None,
        checkpoint=lambda _: None,
    ):
        version(api_version)
        # Bound generators before allocating, and bind consent to the exact batch.
        sources = lin._bounded(sources, lin.SourceRevision)
        outputs = lin._bounded(outputs, lin.OutputRevision)
        feedback = lin._bounded(feedback, lin.Feedback)
        rules = lin._bounded(rules, lin.GoldRule)
        lin._token(collection_evidence)
        batch = (sources, outputs, feedback, rules, seed)
        batch_sha = lin.digest(pack(batch))
        try:
            allowed = (
                verify_collection is not None
                and verify_collection(batch_sha, collection_evidence) is True
            )
        except Exception:
            allowed = False
        if not allowed:
            raise ValueError("collection denied")
        with self.transaction() as tx:
            # Detect immutable event-ID reuse across batches, under the same write lock.
            by_id = {f.feedback_id for f in feedback}
            retained = [
                unpack(row[0])
                for row in tx.execute(
                    "SELECT payload FROM objects WHERE kind='feedback'"
                )
            ]
            prior_conflicts = tuple(
                f for f in retained if f.feedback_id in by_id and f not in feedback
            )
            observations = lin._bounded(feedback + prior_conflicts, lin.Feedback)
            data = lin.build_dataset(
                sources, outputs, observations, rules, verify_actor=verify_actor
            )
            split = lin.lock_split(data, seed=seed)
            snapshot = {
                "batch": batch,
                "dataset": data,
                "split": split,
                "collection_evidence": collection_evidence,
                "batch_sha256": batch_sha,
            }
            identity = lin.digest(pack(snapshot))
            for source in sources:
                family_keys = ["source:" + source.source_id]
                family_keys += [
                    "output:" + o.output_id
                    for o in outputs
                    if (o.source_id, o.source_revision)
                    == (source.source_id, source.revision)
                ]
                for key in family_keys:
                    old = tx.execute(
                        "SELECT family FROM families WHERE identity=?", (key,)
                    ).fetchone()
                    if old and old[0] != source.family_id:
                        raise ValueError("related family rebinding")
                    if not old:
                        self.room(tx, "families")
                        tx.execute(
                            "INSERT INTO families VALUES(?,?)", (key, source.family_id)
                        )
            for kind, values, key in (
                ("source", sources, lambda x: (x.source_id, x.revision)),
                ("output", outputs, lambda x: (x.output_id, x.revision)),
                ("rule", rules, lambda x: (x.task_type, x.rule_id)),
            ):
                for value in values:
                    self.put(tx, kind, json.dumps(key(value)), value)
            # Distinct observations of a conflicting feedback ID are both retained.
            for observation in feedback:
                self.put(tx, "feedback", lin.digest(pack(observation)), observation)
            self.put(tx, "lineage", identity, snapshot)
            checkpoint("before_feedback_commit")
        checkpoint("after_feedback_commit")
        return identity

    def lineage(self, snapshot_sha256):
        lin._hash(snapshot_sha256)
        value = self.get("lineage", snapshot_sha256)
        if lin.digest(pack(value)) != snapshot_sha256:
            raise ValueError("lineage identity changed")
        data, split = value["dataset"], value["split"]
        split.verify(data)
        ev._validate_dataset(data, split)
        return data, split
