"""Explicit local materialization from exact retained eligible lineage.

No remote uploads, optimization, deployment, secret discovery or automatic jobs.
Authority callbacks are trusted pure snapshots, not cross-service atomic locks.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import dataset_contract as c
from .feedback_projection import Observation, RetainedSource
from .feedback_use import UseRequest

Authority = Callable[[dict[str, Any], str, str], bool]


@dataclass(frozen=True)
class Prepared:
    descriptor: dict[str, Any]
    input_bytes: bytes
    output_bytes: bytes
    observation: Observation


def prepare(source: RetainedSource, dataset: str) -> Prepared:
    if type(source) is not RetainedSource:
        raise ValueError("exact retained source required")
    observation = source.observe("local-dataset:" + dataset)
    if (
        observation.state != "active"
        or not observation.authority["collection_allowed"]
        or not observation.authority["current_target"]
    ):
        raise ValueError("current eligible gold required")
    value = source.store.get("lineage", source.snapshot)
    data, split = source.store.lineage(source.snapshot)
    receipt = source.store.get("feedback_collection", value["collection_evidence"])
    selected = [
        feedback
        for row, feedback in zip(receipt["selected"], value["batch"][2])
        if row["selection"] == vars(source.selection)
    ]
    if len(selected) != 1:
        raise ValueError("exact source selection required")
    matches = [example for example in data.examples if selected[0] in example.feedback]
    if len(matches) != 1 or matches[0].gold != matches[0].output.content:
        raise ValueError("exact eligible output required")
    example = matches[0]
    # Preserve original source text exactly as a JSON string, never infer a
    # transformation, task template, semantic deduplication or rewritten answer.
    input_value = example.source.content.decode("utf-8")
    output = example.output.content
    family = c.digest(c.canonical({"refinery_source_family": example.source.family_id}))
    descriptor = c.binding(
        dataset,
        observation.key,
        input_value,
        output,
        family,
        split.partition(example.source.family_id),
    )
    if descriptor["output_sha256"] != selected[0].output_sha256:
        raise ValueError("target artifact changed")
    return Prepared(descriptor, c.canonical(input_value), output, observation)


def verify_use(source: RetainedSource, item: Prepared, purpose: str) -> None:
    value = source.store.get("lineage", source.snapshot)
    source.use.verify(
        source.store,
        UseRequest(
            source.snapshot,
            value["batch_sha256"],
            value["collection_evidence"],
            purpose,
            item.descriptor["item_id"],
            item.observation.key,
        ),
    )


def authorize(check: Authority, item: Prepared, purpose: str, reference: str) -> None:
    if (
        type(reference) is not str
        or not 1 <= len(reference) <= 128
        or not re.fullmatch(r"[A-Za-z0-9_.:-]+", reference)
    ):
        raise ValueError("explicit opaque permission reference required")
    if check(dict(item.descriptor), purpose, reference) is not True:
        raise ValueError("dataset permission denied")


SCHEMA = """
CREATE TABLE items(item_id TEXT PRIMARY KEY,dataset TEXT NOT NULL,source TEXT NOT NULL,family TEXT NOT NULL,input_hash TEXT NOT NULL,split TEXT NOT NULL,descriptor BLOB NOT NULL,input BLOB NOT NULL,output BLOB NOT NULL,receipt TEXT NOT NULL);
CREATE TABLE withdrawals(item_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);
CREATE TRIGGER immutable_items_update BEFORE UPDATE ON items BEGIN SELECT RAISE(ABORT,'immutable item'); END;
CREATE TRIGGER immutable_items_delete BEFORE DELETE ON items BEGIN SELECT RAISE(ABORT,'immutable item'); END;
CREATE TRIGGER immutable_withdrawal_update BEFORE UPDATE ON withdrawals BEGIN SELECT RAISE(ABORT,'immutable withdrawal'); END;
CREATE TRIGGER immutable_withdrawal_delete BEFORE DELETE ON withdrawals BEGIN SELECT RAISE(ABORT,'immutable withdrawal'); END;
"""


class DatasetRegistry:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA max_page_count=32768")
            existing = self.db.execute("SELECT name FROM sqlite_master").fetchall()
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if not existing and version == 0:
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
            self.check()
        except BaseException:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def check(self) -> None:
        if (
            self.db.execute("PRAGMA user_version").fetchone() != (1,)
            or tuple(
                self.db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
                )
            )
            != self.expected_schema
        ):
            raise ValueError("dataset schema changed")

    def available(self, item_id: str) -> None:
        self.check()
        if self.db.execute(
            "SELECT 1 FROM withdrawals WHERE item_id=?", (item_id,)
        ).fetchone():
            raise ValueError("dataset item withdrawn")

    def materialize(
        self,
        source: RetainedSource,
        dataset: str,
        *,
        approve: Authority,
        permission_ref: str,
    ) -> dict[str, Any]:
        item = prepare(source, dataset)
        authorize(approve, item, "materialize", permission_ref)
        verify_use(source, item, "dataset_materialization")
        d = item.descriptor
        self.check()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.available(d["item_id"])
            if self.db.execute(
                "SELECT 1 FROM items WHERE dataset=? AND split!=? AND (source=? OR family=? OR input_hash=?)",
                (
                    dataset,
                    d["split"],
                    d["source_identity_sha256"],
                    d["family_sha256"],
                    d["input_sha256"],
                ),
            ).fetchone():
                raise ValueError("dataset family/input/source crosses split")
            if self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] >= 10000:
                raise ValueError("dataset registry full")
            old = self.db.execute(
                "SELECT descriptor,input,output FROM items WHERE item_id=?",
                (d["item_id"],),
            ).fetchone()
            expected = (c.canonical(d), item.input_bytes, item.output_bytes)
            if old is not None and old != expected:
                raise ValueError("stored dataset bytes changed")
            self.db.execute(
                "INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    d["item_id"],
                    dataset,
                    d["source_identity_sha256"],
                    d["family_sha256"],
                    d["input_sha256"],
                    d["split"],
                    *expected,
                    c.digest(
                        c.canonical({"item": d, "permission_ref": permission_ref})
                    ),
                ),
            )
            authorize(approve, item, "materialize", permission_ref)
            if prepare(source, dataset) != item:
                raise ValueError("dataset source changed during materialization")
            self.available(d["item_id"])
            self.db.execute("COMMIT")
            return dict(d)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def read(
        self,
        item_id: str,
        source: RetainedSource,
        *,
        purpose: str,
        approve: Authority,
        permission_ref: str,
    ) -> tuple[Any, bytes]:
        self.available(item_id)
        row = self.db.execute(
            "SELECT descriptor,input,output FROM items WHERE item_id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown dataset item")
        descriptor = json.loads(row[0])
        item = prepare(source, descriptor["dataset"])
        if item.descriptor["item_id"] != item_id or row != (
            c.canonical(item.descriptor),
            item.input_bytes,
            item.output_bytes,
        ):
            raise ValueError("dataset source/content binding changed")
        if {"optimization": "train", "evaluation": "heldout"}.get(
            purpose
        ) != item.descriptor["split"]:
            raise ValueError("dataset purpose crosses locked split")
        authorize(approve, item, purpose, permission_ref)
        verify_use(source, item, purpose)
        authorize(approve, item, purpose, permission_ref)
        if prepare(source, descriptor["dataset"]) != item:
            raise ValueError("dataset source changed during use")
        self.available(item_id)
        return json.loads(item.input_bytes), item.output_bytes

    def withdraw(
        self, item_id: str, *, approve: Authority, permission_ref: str
    ) -> None:
        self.check()
        row = self.db.execute(
            "SELECT descriptor FROM items WHERE item_id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown dataset item")
        descriptor = json.loads(row[0])
        if (
            type(permission_ref) is not str
            or not 1 <= len(permission_ref) <= 128
            or not re.fullmatch(r"[A-Za-z0-9_.:-]+", permission_ref)
        ):
            raise ValueError("explicit withdrawal reference required")
        if approve(dict(descriptor), "withdraw", permission_ref) is not True:
            raise ValueError("withdrawal denied")
        self.db.execute(
            "INSERT OR IGNORE INTO withdrawals VALUES(?,?)",
            (
                item_id,
                c.digest(
                    c.canonical({"item": descriptor, "permission_ref": permission_ref})
                ),
            ),
        )
