"""Explicit local materialization from exact retained eligible lineage.

No remote uploads, optimization, deployment, secret discovery or automatic jobs.
Authority callbacks are trusted pure snapshots, not cross-service atomic locks.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import dataset_contract as c
from .feedback_projection import Observation, RetainedSource
from .feedback_use import use_guard
from .heldout_policy import check as check_holdout

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


def cohort_check(source: RetainedSource, item: Prepared, purpose: str) -> None:
    value = source.store.get("lineage", source.snapshot)
    check_holdout(
        source.store,
        value["dataset"],
        value["split"],
        purpose=purpose,
        configuration_sha256=item.descriptor["item_id"],
        owner=None,
    )


def verify_use(source: RetainedSource, item: Prepared, purpose: str) -> None:
    use_guard(
        source.store,
        source.snapshot,
        purpose=purpose,
        configuration_sha256=item.descriptor["item_id"],
        run_sha256=item.observation.key,
        retained_use=source.use,
        synthetic_fixture=False,
        policy_owner=None,
    )()


def provenance_receipt(source: RetainedSource, item: Prepared, reference: str) -> bytes:
    value = source.store.get("lineage", source.snapshot)
    return c.canonical(
        dict(
            version="refinery.materialization-receipt.v1",
            item_id=item.descriptor["item_id"],
            snapshot_sha256=source.snapshot,
            collection_receipt_sha256=value["collection_evidence"],
            batch_sha256=value["batch_sha256"],
            selected_event_sha256=source.selection.event_sha256,
            source_observation_sha256=item.observation.provenance,
            permission_reference_sha256=c.digest(reference.encode("ascii")),
            purpose="materialize",
            permission_scope="historical_not_current_authority",
        )
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
CREATE TABLE items(item_id TEXT PRIMARY KEY,dataset TEXT NOT NULL,source TEXT NOT NULL,family TEXT NOT NULL,input_hash TEXT NOT NULL,split TEXT NOT NULL,descriptor BLOB NOT NULL,input BLOB NOT NULL,output BLOB NOT NULL,receipt BLOB NOT NULL);
CREATE TABLE withdrawals(item_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);
CREATE TRIGGER immutable_items_update BEFORE UPDATE ON items BEGIN SELECT RAISE(ABORT,'immutable item'); END;
CREATE TRIGGER immutable_items_delete BEFORE DELETE ON items BEGIN SELECT RAISE(ABORT,'immutable item'); END;
CREATE TRIGGER immutable_withdrawal_update BEFORE UPDATE ON withdrawals BEGIN SELECT RAISE(ABORT,'immutable withdrawal'); END;
CREATE TRIGGER immutable_withdrawal_delete BEFORE DELETE ON withdrawals BEGIN SELECT RAISE(ABORT,'immutable withdrawal'); END;
"""


MAX_ITEMS = 10000


def private_file(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        raise ValueError("private owned regular dataset file required")
    return info.st_dev, info.st_ino


class DatasetRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        parent = self.path.parent
        if not self.path.is_absolute() or parent.resolve(strict=True) != parent:
            raise ValueError("canonical private dataset directory required")
        info = parent.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_uid != os.geteuid()
        ):
            raise ValueError("private owned dataset directory required")
        # Reject existing unsafe files; never chmod someone else's database or
        # globally change umask. The owned private directory excludes other users.
        try:
            fd = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self.file_identity = private_file(self.path)
        self.check_files()
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        try:
            self.check_files()
            existing = self.db.execute("SELECT name FROM sqlite_master").fetchall()
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
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
            if not existing and version == 0:
                self.db.executescript(SCHEMA)
                self.db.execute("PRAGMA user_version=1")
            self.check()
            # Only an admitted schema receives writable journaling settings.
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA max_page_count=32768")
            self.check_files()
        except BaseException:
            self.db.close()
            raise

    def check_files(self) -> None:
        parent = self.path.parent
        info = parent.stat()
        if (
            parent.resolve(strict=True) != parent
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_uid != os.geteuid()
            or private_file(self.path) != self.file_identity
        ):
            raise ValueError("dataset private storage identity changed")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                private_file(sidecar)

    def close(self) -> None:
        self.db.close()

    def check(self) -> None:
        self.check_files()
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
            old = self.db.execute(
                "SELECT descriptor,input,output FROM items WHERE item_id=?",
                (d["item_id"],),
            ).fetchone()
            if (
                old is None
                and self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                >= MAX_ITEMS
            ):
                raise ValueError("dataset registry full")
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
                    provenance_receipt(source, item, permission_ref),
                ),
            )
            authorize(approve, item, "materialize", permission_ref)
            if prepare(source, dataset) != item:
                raise ValueError("dataset source changed during materialization")
            cohort_check(source, item, "dataset_materialization")
            self.available(d["item_id"])
            self.db.execute("COMMIT")
            return dict(d)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _bound_item(self, item_id: str, source: RetainedSource) -> Prepared:
        """Integrity mapping only; callers must enforce current purpose authority."""
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
        return item

    def read(
        self,
        item_id: str,
        source: RetainedSource,
        *,
        purpose: str,
        approve: Authority,
        permission_ref: str,
    ) -> tuple[Any, bytes]:
        item = self._bound_item(item_id, source)
        if {"optimization": "train", "evaluation": "heldout"}.get(
            purpose
        ) != item.descriptor["split"]:
            raise ValueError("dataset purpose crosses locked split")
        authorize(approve, item, purpose, permission_ref)
        verify_use(source, item, purpose)
        authorize(approve, item, purpose, permission_ref)
        if prepare(source, item.descriptor["dataset"]) != item:
            raise ValueError("dataset source changed during use")
        cohort_check(source, item, purpose)
        self.available(item_id)
        return json.loads(item.input_bytes), item.output_bytes

    def export_read(
        self,
        item_id: str,
        source: RetainedSource,
        *,
        destination: dict[str, Any],
        approve: Authority,
        permission_ref: str,
    ) -> tuple[dict[str, Any], Any, bytes]:
        """Exact transient content export; remote authority never authorizes use."""
        from .dataset_batch import Selection, source_fence
        from .dataset_export import Destination

        if type(destination) is not dict or set(destination) != {
            "origin",
            "project_id",
            "dataset_id",
            "dataset_name",
            "split",
            "status",
        }:
            raise ValueError("exact export destination required")
        expected_destination = Destination(
            **{k: v for k, v in destination.items() if k != "status"}
        ).value()
        if destination != expected_destination:
            raise ValueError("exact export destination required")
        item = self._bound_item(item_id, source)
        encoded_destination = c.canonical(destination)
        if (
            type(destination) is not dict
            or destination.get("split") != item.descriptor["split"]
        ):
            raise ValueError("export destination crosses split")
        if type(permission_ref) is not str or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", permission_ref
        ):
            raise ValueError("explicit export permission reference required")
        request = dict(item=item.descriptor, destination=destination)
        encoded = c.canonical(request)

        def authorize_now() -> None:
            if (
                approve(json.loads(encoded), "dataset_export", permission_ref)
                is not True
            ):
                raise ValueError("dataset content export denied")
            if c.canonical(destination) != encoded_destination:
                raise ValueError("export destination changed")

        authorize_now()
        verify_use(source, item, "dataset_export")
        if self._bound_item(item_id, source) != item:
            raise ValueError("export source changed")
        authorize_now()
        source_fence(Selection(item_id, source))
        cohort_check(source, item, "dataset_export")
        self.available(item_id)
        return dict(item.descriptor), json.loads(item.input_bytes), item.output_bytes

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
