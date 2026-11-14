"""Exact component-owned schema admission; no migrations or silent repairs."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Sequence


def _shape(db: sqlite3.Connection) -> list[tuple[object, ...]]:
    return list(
        db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    )


def validate(
    db: sqlite3.Connection, statements: Sequence[str], decode: Callable[[bytes], object]
) -> None:
    """Call inside the same write transaction as the subsequent use/commit.

    Exact SQL intentionally rejects equivalent but unrecognized custom layouts.
    Trusted callbacks must not commit/rollback this component's connection.
    This is admission/corruption detection, not malicious SQLite file isolation.
    """
    if db.execute("PRAGMA user_version").fetchone() != (1,):
        raise ValueError("incompatible refinery store")
    expected = sqlite3.connect(":memory:")
    try:
        for sql in statements:
            expected.execute(sql)
        for table in ("objects", "families"):
            for action in ("UPDATE", "DELETE"):
                expected.execute(
                    f"CREATE TRIGGER {table}_{action} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT,'immutable retained record'); END"
                )
        if _shape(db) != _shape(expected):
            raise ValueError("incompatible refinery store")
        # Independent metadata comparisons also cover column order/types/keys,
        # index uniqueness and indexed column order (including autoindexes).
        for (name,) in expected.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ):
            for pragma in ("table_xinfo", "index_list", "foreign_key_list"):
                if list(db.execute(f"PRAGMA {pragma}('{name}')")) != list(
                    expected.execute(f"PRAGMA {pragma}('{name}')")
                ):
                    raise ValueError("incompatible refinery store")
        for (name,) in expected.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ):
            if list(db.execute(f"PRAGMA index_xinfo('{name}')")) != list(
                expected.execute(f"PRAGMA index_xinfo('{name}')")
            ):
                raise ValueError("incompatible refinery store")
    finally:
        expected.close()
    # Aggregate SQL bounds before fetching any potentially corrupt payload.
    if db.execute(
        "SELECT count(*) FROM (SELECT kind FROM objects GROUP BY kind HAVING count(*)>1000)"
    ).fetchone()[0]:
        raise ValueError("refinery record count limit")
    for table, columns, limit in (
        ("objects", ("payload",), None),
        ("budgets", ("payload",), 1000),
        ("experiments", ("binding",), 1000),
        ("attempts", ("request", "receipt"), 4096),
        ("families", (), 1000),
        ("environments", (), 1000),
        ("activations", (), 1000),
    ):
        if (
            limit is not None
            and db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > limit
        ):
            raise ValueError("refinery record count limit")
        for column in columns:
            count, maximum, total_bytes = db.execute(
                f"SELECT count(*),coalesce(max(length({column})),0),coalesce(sum(length({column})),0) FROM {table}"
            ).fetchone()
            if maximum > 1_048_576 or total_bytes > 67_108_864 or count > 16_777_216:
                raise ValueError("refinery payload limit")
            if table != "objects":
                for (payload,) in db.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                ):
                    if type(payload) is not bytes:
                        raise ValueError("corrupt refinery payload")
                    decode(payload)
    total = 0
    for kind, identity, digest, payload in db.execute(
        "SELECT kind,identity,digest,payload FROM objects"
    ):
        if not (
            type(kind) is str
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", kind) is not None
            and type(identity) is str
            and 0 < len(identity) <= 4096
            and type(digest) is str
            and type(payload) is bytes
            and len(payload) <= 1_048_576
        ):
            raise ValueError("corrupt refinery record")
        total += len(payload)
        if total > 67_108_864 or hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("corrupt refinery record")
        decode(payload)
    for identity, family in db.execute("SELECT identity,family FROM families"):
        if not (
            type(identity) is str
            and identity.startswith(("source:", "output:"))
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identity.split(":", 1)[1])
            is not None
            and type(family) is str
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", family) is not None
        ):
            raise ValueError("corrupt refinery family")
