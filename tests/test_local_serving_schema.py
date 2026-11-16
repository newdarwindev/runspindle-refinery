"""Disposable real SQLite stores; no production adapter or provider qualification."""

import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import serving
from refinery.lineage import digest

ROOT = Path(__file__).resolve().parents[1]
CONTENT = b'{"format":"synthetic.map.v1","responses":{"hello":"first"}}'


class LocalServingSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "serving.sqlite"

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(db.close)
        return db

    def canonical(self) -> sqlite3.Connection:
        db = self.connect()
        # Independent literal legacy constructor DDL, not the new schema constant.
        db.execute(
            "CREATE TABLE IF NOT EXISTS active(environment TEXT PRIMARY KEY, activation TEXT, generation INTEGER, digest TEXT, content BLOB)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, action TEXT, activation TEXT, digest TEXT)"
        )
        return db

    def test_empty_nonzero_version_does_not_initialize(self) -> None:
        db = self.connect()
        db.execute("PRAGMA user_version=1")
        with self.assertRaisesRegex(ValueError, "incompatible local serving schema"):
            serving.LocalServing(self.path)
        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master").fetchone(), (0,))
        self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))

    def test_partial_store_rejected_without_repair_or_connection_leak(self) -> None:
        db = self.connect()
        db.execute(serving.LOCAL_STATEMENTS[0])
        before = tuple(db.iterdump())
        opened: list[sqlite3.Connection] = []
        connect = sqlite3.connect

        def capture(path: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
            connection = connect(path, *args, **kwargs)
            opened.append(connection)
            return connection

        with patch.object(sqlite3, "connect", capture):
            with self.assertRaisesRegex(ValueError, "incompatible local serving schema"):
                serving.LocalServing(self.path)
        self.assertEqual(tuple(db.iterdump()), before)
        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("delete",))
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_unknown_layouts_and_versions_rejected_without_logical_changes(self) -> None:
        changes = (
            "DROP TABLE active",
            "DROP TABLE calls",
            "ALTER TABLE active ADD COLUMN extra TEXT",
            "CREATE INDEX extra ON active(digest)",
            "CREATE VIEW extra AS SELECT * FROM active",
            "CREATE TABLE extra(value TEXT)",
            "CREATE TRIGGER extra BEFORE INSERT ON active BEGIN SELECT RAISE(IGNORE); END",
            "PRAGMA user_version=1",
        )
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                path = self.path.with_name(f"invalid-{index}.sqlite")
                db = sqlite3.connect(path, isolation_level=None)
                try:
                    for statement in serving.LOCAL_STATEMENTS:
                        db.execute(statement)
                    db.execute(change)
                    before = tuple(db.iterdump())
                    with self.assertRaises(ValueError):
                        serving.LocalServing(path)
                    self.assertEqual(tuple(db.iterdump()), before)
                finally:
                    db.close()

    def test_missing_uniqueness_and_changed_constraints_deny_admission(self) -> None:
        for index, replacement in enumerate(
            ("environment TEXT", "environment TEXT UNIQUE")
        ):
            path = self.path.with_name(f"key-{index}.sqlite")
            db = sqlite3.connect(path, isolation_level=None)
            try:
                db.execute(
                    serving.LOCAL_STATEMENTS[0].replace(
                        "environment TEXT PRIMARY KEY", replacement
                    )
                )
                db.execute(serving.LOCAL_STATEMENTS[1])
                before = tuple(db.iterdump())
                with self.assertRaisesRegex(ValueError, "incompatible local serving schema"):
                    adapter = serving.LocalServing(path)
                    self.addCleanup(adapter.close)
                self.assertEqual(tuple(db.iterdump()), before)
            finally:
                db.close()

    def test_postopen_schema_drift_denies_load_readback_and_predict(self) -> None:
        adapter = serving.LocalServing(self.path)
        self.addCleanup(adapter.close)
        adapter.load("first", "local", 1, digest(CONTENT), CONTENT, None)
        db = self.connect()
        db.execute("CREATE VIEW extra AS SELECT * FROM active")
        before = tuple(db.iterdump())
        for call in (
            lambda: adapter.load(
                "second", "local", 2, digest(CONTENT), CONTENT, digest(CONTENT)
            ),
            lambda: adapter.readback("local"),
            lambda: adapter.predict("local", "hello"),
        ):
            with self.assertRaises(ValueError):
                call()
            self.assertEqual(tuple(db.iterdump()), before)
        self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0], 1)

    def test_temporary_shadow_cannot_redirect_loader_operations(self) -> None:
        adapter = serving.LocalServing(self.path)
        self.addCleanup(adapter.close)
        adapter.db.execute(
            "CREATE TEMP TABLE active(environment TEXT, activation TEXT, generation INTEGER, digest TEXT, content BLOB)"
        )
        for call in (
            lambda: adapter.load("first", "local", 1, digest(CONTENT), CONTENT, None),
            lambda: adapter.readback("local"),
            lambda: adapter.predict("local", "hello"),
        ):
            with self.assertRaises(ValueError):
                call()
        for table in ("main.active", "temp.active", "main.calls"):
            self.assertEqual(
                adapter.db.execute(f"SELECT count(*) FROM {table}").fetchone(), (0,)
            )

    def test_precommit_schema_change_rolls_back_load_and_ddl(self) -> None:
        adapter = serving.LocalServing(self.path)
        self.addCleanup(adapter.close)
        before = tuple(adapter.db.iterdump())
        validate = serving._validate_local_schema
        checks = 0

        def drift(db: sqlite3.Connection) -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                db.execute("CREATE VIEW extra AS SELECT * FROM active")
            validate(db)

        with patch.object(serving, "_validate_local_schema", drift):
            with self.assertRaises(ValueError):
                adapter.load("first", "local", 1, digest(CONTENT), CONTENT, None)
        self.assertEqual(tuple(adapter.db.iterdump()), before)

    def test_legacy_version_zero_reopens_in_fresh_process(self) -> None:
        self.canonical()
        adapter = serving.LocalServing(self.path)
        adapter.load("first", "local", 1, digest(CONTENT), CONTENT, None)
        adapter.close()
        code = """
import json, socket, sys
from unittest.mock import patch
from refinery.serving import LocalServing
with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')) as network:
 adapter=LocalServing(sys.argv[1])
 try:
  with patch.object(adapter,'load',side_effect=AssertionError('reload forbidden')) as load:
   output=adapter.predict('local','hello')
   print(json.dumps({'output':output,'readback':adapter.readback('local'),
                    'version':adapter.db.execute('PRAGMA user_version').fetchone()[0],
                    'calls':adapter.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
   load.assert_not_called(); network.assert_not_called()
 finally: adapter.close()
"""
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            result = subprocess.run(
                [sys.executable, "-O", "-c", code, str(self.path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["version"], 0)
        self.assertEqual(receipt["calls"], 1)
        self.assertEqual(
            receipt["output"], {"output": "first", "candidate_sha256": digest(CONTENT)}
        )
        self.assertEqual(receipt["readback"]["activation_id"], "first")

    def test_removed_schema_guard_is_detected(self) -> None:
        with patch.object(serving, "_validate_local_schema", lambda _: None):
            result = unittest.TestResult()
            LocalServingSchemaTests(
                "test_missing_uniqueness_and_changed_constraints_deny_admission"
            ).run(result)
        self.assertFalse(result.errors, repr(result.errors))
        self.assertTrue(result.failures, "missing uniqueness escaped negative regression")


if __name__ == "__main__":
    unittest.main()
