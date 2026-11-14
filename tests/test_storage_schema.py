"""Actual SQLite structural admission and transaction rollback regressions."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import test_durable

from refinery import storage

st: Any = storage
setup: Any = test_durable.setup


class SchemaTests(unittest.TestCase):
    def test_missing_tables_columns_indexes_and_triggers_deny_reopen(self) -> None:
        cases = [
            "DROP TABLE families",
            "DROP TRIGGER objects_UPDATE",
            "DROP TRIGGER families_DELETE",
            "ALTER TABLE objects ADD COLUMN unexpected TEXT",
            "CREATE INDEX extra_index ON objects(digest)",
            "CREATE VIEW hidden_view AS SELECT * FROM objects",
            "PRAGMA user_version=2",
            "DROP TABLE activations; CREATE TABLE activations(identity TEXT PRIMARY KEY, environment TEXT NOT NULL, generation INTEGER NOT NULL, candidate TEXT NOT NULL, predecessor TEXT, state TEXT NOT NULL, approval TEXT NOT NULL)",
            "DROP TRIGGER objects_UPDATE; CREATE TRIGGER objects_UPDATE BEFORE UPDATE ON objects BEGIN SELECT 1; END",
        ]
        for sql in cases:
            with self.subTest(sql=sql), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "db"
                with st.Store(path):
                    pass
                with sqlite3.connect(path) as db:
                    db.executescript(sql)
                    before = list(
                        db.execute(
                            "SELECT type,name,sql FROM sqlite_master ORDER BY name"
                        )
                    )
                with self.assertRaises(ValueError):
                    st.Store(path)
                with sqlite3.connect(path) as db:
                    self.assertEqual(
                        before,
                        list(
                            db.execute(
                                "SELECT type,name,sql FROM sqlite_master ORDER BY name"
                            )
                        ),
                    )

    def test_corrupt_payloads_and_family_rows_deny(self) -> None:
        rows = [
            ("objects", ("valid", "id", "0" * 64, st.pack("value"))),
            ("objects", ("valid", "id", "0" * 64, b"x" * 1048577)),
            ("families", ("source:id", "\ninvalid")),
            ("budgets", ("budget", b"not canonical JSON")),
        ]
        for table, row in rows:
            with tempfile.TemporaryDirectory() as root:
                path = Path(root) / "db"
                with st.Store(path):
                    pass
                with sqlite3.connect(path) as db:
                    db.execute(
                        f"INSERT INTO {table} VALUES({','.join('?' for _ in row)})", row
                    )
                with self.assertRaises(ValueError):
                    st.Store(path)

    def test_oversized_blob_is_rejected_before_decoder(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "db"
            with st.Store(path):
                pass
            with sqlite3.connect(path) as db:
                db.execute(
                    "INSERT INTO objects VALUES('x','id','bad',zeroblob(1048577))"
                )
            with patch.object(
                st, "unpack", side_effect=AssertionError("must not decode")
            ):
                with self.assertRaisesRegex(ValueError, "payload limit"):
                    st.Store(path)

    def test_transaction_rechecks_external_schema_change_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "db"
            with st.Store(path) as store:
                with sqlite3.connect(path) as other:
                    other.execute("DROP TRIGGER families_UPDATE")
                with self.assertRaises(ValueError):
                    with store.transaction() as tx:
                        st.Store.put(tx, "receipt", "id", {"approved": True})
                self.assertEqual(
                    store.db.execute("SELECT count(*) FROM objects").fetchone(), (0,)
                )

    def test_commit_time_ddl_or_corruption_rolls_back_lineage_and_receipt(self) -> None:
        for mutation in (
            "DROP TABLE families",
            "INSERT INTO families VALUES('source:bad','')",
        ):
            with (
                tempfile.TemporaryDirectory() as root,
                st.Store(Path(root) / "db") as store,
            ):

                def checkpoint(point: str) -> None:
                    if point == "before_feedback_commit":
                        st.Store.put(
                            store.db,
                            "collection_provenance",
                            "receipt",
                            {"approved": True},
                        )
                        store.db.execute(mutation)

                # Use real existing collector fixtures and add the commit callback.
                original = store.commit_lineage

                def commit(*args: object, **kw: object) -> object:
                    kw["checkpoint"] = checkpoint
                    return original(*args, **kw)

                with patch.object(store, "commit_lineage", commit):
                    with self.assertRaises(ValueError):
                        setup(store)
                self.assertEqual(
                    store.db.execute("SELECT count(*) FROM objects").fetchone(), (0,)
                )
                self.assertEqual(
                    store.db.execute("SELECT count(*) FROM families").fetchone(), (0,)
                )

    def test_fresh_process_supported_store_and_concurrent_open(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "db"
            with st.Store(path) as store:
                snapshot = setup(store)[0]
            code = 'import sys,json;from refinery.storage import Store; s=Store(sys.argv[1]);print(json.dumps(s.db.execute("SELECT count(*) FROM objects").fetchone()));s.close()'
            run = subprocess.run(
                [sys.executable, "-c", code, str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertGreater(json.loads(run.stdout)[0], 0)

            def read(_: int) -> bool:
                with st.Store(path) as store:
                    return bool(store.lineage(snapshot))

            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(read, range(8))), [True] * 8)

    def test_removed_schema_guard_mutation_exposes_missing_table(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "db"
            with st.Store(path):
                pass
            with sqlite3.connect(path) as db:
                db.execute("DROP TABLE families")
            with self.assertRaises(ValueError):
                st.Store(path)
            with patch.object(st.Store, "_validate", staticmethod(lambda _: None)):
                with st.Store(path) as store:
                    with self.assertRaises(AssertionError):
                        self.assertIsNotNone(
                            store.db.execute(
                                "SELECT name FROM sqlite_master WHERE name='families'"
                            ).fetchone()
                        )


if __name__ == "__main__":
    unittest.main()
