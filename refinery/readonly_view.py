"""Bound short-lived SQLite views for existing component-owned stores.

No database creation, shared-thread handles, immutable snapshots or authority.
Only the supplied store's existing read methods are used by callers.
"""

from __future__ import annotations

import copy
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def schema(db: sqlite3.Connection) -> tuple[Any, ...]:
    return (
        db.execute("PRAGMA user_version").fetchone(),
        db.execute("PRAGMA application_id").fetchone(),
        tuple(
            db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name")
        ),
    )


def identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
    ):
        raise ValueError("owned canonical SQLite file required")
    return info.st_dev, info.st_ino


class ReadView:
    """Capture identity on its connection-owning thread; open on using thread."""

    def __init__(self, store: Any, expected_type: type[Any]) -> None:
        if type(store) is not expected_type or type(store.db) is not sqlite3.Connection:
            raise ValueError("exact component SQLite store required")
        if store.db.in_transaction:
            raise ValueError("cannot bind an active store transaction")
        rows = store.db.execute("PRAGMA database_list").fetchall()
        if (
            not rows
            or rows[0][1] != "main"
            or not rows[0][2]
            or rows[1:] not in ([], [(1, "temp", "")])
            or store.db.execute("SELECT name FROM sqlite_temp_master").fetchall()
        ):
            raise ValueError("one durable SQLite file required")
        self.store, self.connection, self.expected_type = store, store.db, expected_type
        self.path = Path(rows[0][2])
        self.file_identity = identity(self.path)
        self.expected_schema = schema(store.db)

    @contextmanager
    def open(self) -> Any:
        if (
            type(self.store) is not self.expected_type
            or self.store.db is not self.connection
        ):
            raise ValueError("bound store handle changed")
        if identity(self.path) != self.file_identity:
            raise ValueError("bound store file changed")
        # mode=ro, not immutable=1: every fresh transaction must observe WAL.
        db = sqlite3.connect(
            self.path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5
        )
        try:
            db.execute("PRAGMA query_only=ON")
            if (
                identity(self.path) != self.file_identity
                or schema(db) != self.expected_schema
            ):
                raise ValueError("bound store admission changed")
            view = copy.copy(self.store)
            view.db = db
            yield view
            if db.in_transaction:
                raise ValueError("read view left active transaction")
            if (
                identity(self.path) != self.file_identity
                or schema(db) != self.expected_schema
            ):
                raise ValueError("bound store changed during use")
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def seal(self) -> tuple[Any, ...]:
        return (
            self.expected_type,
            id(self.store),
            id(self.connection),
            str(self.path),
            self.file_identity,
            self.expected_schema,
        )
