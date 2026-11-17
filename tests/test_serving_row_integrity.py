"""Bounded persisted local serving identity; no provider or production claim."""

import inspect
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import serving
from refinery.lineage import MAX_BYTES, digest

ROOT = Path(__file__).resolve().parents[1]
CONTENT = b'{"format":"synthetic.map.v1","responses":{"hello":"first"}}'
SECOND = b'{"format":"synthetic.map.v1","responses":{"hello":"second"}}'


class StoredServingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "serving.sqlite"
        self.adapter = serving.LocalServing(self.path)
        self.addCleanup(self.adapter.close)
        self.adapter.load("first", "local", 1, digest(CONTENT), CONTENT, None)
        network = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(spy.assert_not_called)

    def snapshot(self) -> tuple[Any, ...]:
        # SQL dump quoting truncates NUL-containing TEXT; observe exact values.
        return (
            tuple(
                self.adapter.db.execute(
                    "SELECT environment,activation,generation,digest,content FROM active ORDER BY environment"
                )
            ),
            tuple(self.adapter.db.execute("SELECT * FROM calls ORDER BY id")),
        )

    def assert_denied_without_repair(self) -> None:
        before = self.snapshot()
        for operation in (
            lambda: self.adapter.readback("local"),
            lambda: self.adapter.predict("local", "hello"),
            lambda: self.adapter.load(
                "second", "local", 2, digest(SECOND), SECOND, digest(CONTENT)
            ),
            lambda: self.adapter.load(
                "first", "local", 1, digest(CONTENT), CONTENT, None
            ),
        ):
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(
                self.adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (1,)
            )

    def test_corrupt_identity_or_bytes_block_observation_and_replacement(self) -> None:
        cases = (
            ("activation", None),
            ("activation", ""),
            ("activation", "bad name"),
            ("activation", "a" * 129),
            ("generation", None),
            ("generation", -1),
            ("generation", 0),
            ("generation", 1.5),
            ("generation", "unknown"),
            ("digest", None),
            ("digest", "0" * 64),
            ("content", CONTENT.decode()),
            ("content", None),
            ("content", b""),
            ("content", b"changed"),
        )
        for column, value in cases:
            with self.subTest(column=column, value=value):
                self.adapter.db.execute(
                    "UPDATE active SET activation='first',generation=1,digest=?,content=?",
                    (digest(CONTENT), CONTENT),
                )
                self.adapter.db.execute(f"UPDATE active SET {column}=?", (value,))
                self.assert_denied_without_repair()

    def test_corrupt_program_with_matching_digest_is_not_repaired_by_load(self) -> None:
        invalid = b'{"format":"other","responses":{"hello":"invalid"}}'
        self.adapter.db.execute(
            "UPDATE active SET content=?,digest=?", (invalid, digest(invalid))
        )
        self.assert_denied_without_repair()

    def test_oversized_blob_is_not_materialized_and_is_preserved(self) -> None:
        large = b"x" * (MAX_BYTES + 1)
        self.adapter.db.execute(
            "UPDATE active SET content=?,digest=?", (large, digest(large))
        )
        projected: list[Any] = []

        def observe(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
            if cursor.description[0][0] == "activation":
                projected.append(row)
            return row

        self.adapter.db.row_factory = observe
        self.assert_denied_without_repair()
        self.assertEqual(len(projected), 4)
        self.assertTrue(all(row[3] is None for row in projected))
        self.assertEqual(
            self.adapter.db.execute("SELECT length(content) FROM active").fetchone(),
            (MAX_BYTES + 1,),
        )

    def test_nul_suffixed_oversized_identity_is_not_materialized(self) -> None:
        for column, prefix, offset in (
            ("activation", "first", 0),
            ("digest", digest(CONTENT), 2),
        ):
            with self.subTest(column=column):
                self.adapter.db.row_factory = None
                self.adapter.db.execute(
                    "UPDATE active SET activation='first',digest=?", (digest(CONTENT),)
                )
                value = prefix + "\0" + "x" * (MAX_BYTES + 1)
                self.adapter.db.execute(f"UPDATE active SET {column}=?", (value,))
                projected: list[Any] = []

                def observe(
                    cursor: sqlite3.Cursor, row: tuple[Any, ...]
                ) -> tuple[Any, ...]:
                    if cursor.description[0][0] == "activation":
                        projected.append(row)
                    return row

                self.adapter.db.row_factory = observe
                self.assert_denied_without_repair()
                self.assertEqual(len(projected), 4)
                self.assertTrue(all(row[offset] is None for row in projected))
                self.assertEqual(
                    self.adapter.db.execute(
                        f"SELECT length(CAST({column} AS BLOB)) FROM active"
                    ).fetchone(),
                    (len(value),),
                )

    def test_fresh_process_rejects_persisted_identity_and_preserves_rows(self) -> None:
        self.adapter.db.execute("UPDATE active SET generation=-1")
        before = self.snapshot()
        code = """
import json,socket,sys
from unittest.mock import patch
from refinery.serving import LocalServing
from refinery.lineage import digest
content = b'{"format":"synthetic.map.v1","responses":{"hello":"second"}}'
a=LocalServing(sys.argv[1]); rejected=[]
try:
 with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')) as network:
  for action in (lambda:a.readback('local'),lambda:a.predict('local','hello'),lambda:a.load('second','local',2,digest(content),content,sys.argv[2])):
   try: action()
   except ValueError: rejected.append(True)
  network.assert_not_called()
  print(json.dumps({'rejected':rejected,'calls':a.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
finally: a.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.path), digest(CONTENT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout), {"rejected": [True] * 3, "calls": 1}
        )
        self.assertEqual(self.snapshot(), before)

    def test_healthy_replay_replacement_and_prediction_keep_exact_attribution(
        self,
    ) -> None:
        self.adapter.load("first", "local", 1, digest(CONTENT), CONTENT, None)
        self.assertEqual(
            self.adapter.predict("local", "hello"),
            {"output": "first", "candidate_sha256": digest(CONTENT)},
        )
        self.adapter.load("second", "local", 2, digest(SECOND), SECOND, digest(CONTENT))
        self.assertEqual(
            self.adapter.readback("local"),
            {
                "activation_id": "second",
                "generation": 2,
                "candidate_sha256": digest(SECOND),
            },
        )
        self.assertEqual(
            self.adapter.predict("local", "hello"),
            {"output": "second", "candidate_sha256": digest(SECOND)},
        )

    def test_removed_generation_guard_exposes_output_from_corrupt_identity(
        self,
    ) -> None:
        source = textwrap.dedent(inspect.getsource(serving.LocalServing._active_row))
        self.assertEqual(source.count("_integer(row[1], positive=True)"), 1)
        mutated = source.replace(
            "_integer(row[1], positive=True)", "pass  # deliberate defect"
        )
        scope = dict(vars(serving))
        exec(compile(mutated, "<deliberate-row-guard-mutation>", "exec"), scope)
        self.adapter.db.execute("UPDATE active SET generation=-1")
        with self.assertRaises(ValueError):
            self.adapter.predict("local", "hello")
        with patch.object(serving.LocalServing, "_active_row", scope["_active_row"]):
            with self.assertRaises(AssertionError):
                with self.assertRaises(ValueError):
                    self.adapter.predict("local", "hello")
            self.assertEqual(self.adapter.predict("local", "hello")["output"], "first")
        self.assertEqual(
            self.adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (1,)
        )


if __name__ == "__main__":
    unittest.main()
