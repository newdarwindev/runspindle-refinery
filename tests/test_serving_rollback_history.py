"""Exact serving history and retained-approval rollback checks."""

import importlib
import inspect
import sqlite3
import textwrap
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import serving
from refinery.lineage import digest
from refinery.serving import Approval, Registry
from refinery.storage import VERSION, pack

fixtures = importlib.import_module("test_durable")


class RollbackHistoryTests(unittest.TestCase):
    setUp: Any = fixtures.DurableBase.setUp
    candidate: Any = fixtures.ServingTests.candidate
    adapter: Any = fixtures.ServingTests.adapter
    path: Path

    def test_removing_final_history_check_permits_invalid_rollback(self) -> None:
        source = textwrap.dedent(inspect.getsource(Registry._begin))
        anchor = "if rollback_history is not None:"
        self.assertEqual(source.count(anchor), 1)
        namespace = dict(vars(serving))
        exec(source.replace(anchor, "if False:"), namespace)
        with patch.object(Registry, "_begin", namespace["_begin"]):
            result = unittest.TestResult()
            RollbackHistoryTests(
                "test_rollback_rejects_history_changes_during_approval"
            ).run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("ValueError not raised", result.failures[0][1])

    def test_rollback_rejects_history_changes_during_approval(self) -> None:
        registry, first, approval = self.candidate()
        adapter = self.adapter()
        registry.begin(VERSION, "first", first, approval, expected_generation=0)
        self.assertEqual(registry.load(VERSION, "first", adapter), "active")
        _, second, grant = self.candidate("two")
        registry.begin(VERSION, "second", second, grant, expected_generation=1)
        self.assertEqual(registry.load(VERSION, "second", adapter), "active")
        observed = adapter.readback("local")

        def change_history(value: Approval) -> str:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    "UPDATE activations SET predecessor=NULL WHERE identity='second'"
                )
            return value.actor

        registry.verify_approval = change_history
        with self.assertRaisesRegex(ValueError, "rollback"):
            registry.rollback(VERSION, "rollback", "local", expected_generation=2)
        self.assertEqual(
            registry.state("local"),
            {
                "generation": 2,
                "active": second,
                "pending": None,
            },
        )
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT identity FROM activations ORDER BY generation"
                ).fetchall(),
                [("first",), ("second",)],
            )
        self.assertEqual(adapter.readback("local"), observed)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (2,)
        )

    def test_rollback_cannot_skip_the_immediate_served_predecessor(self) -> None:
        adapter = self.adapter()
        registry, first, first_approval = self.candidate()
        registry.begin(VERSION, "first", first, first_approval, expected_generation=0)
        self.assertEqual(registry.load(VERSION, "first", adapter), "active")
        for generation, name in enumerate(("second", "third"), start=1):
            _, key, approval = self.candidate(name)
            registry.begin(VERSION, name, key, approval, expected_generation=generation)
            self.assertEqual(registry.load(VERSION, name, adapter), "active")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE activations SET predecessor=? WHERE identity='third'", (first,)
            )
        observed = adapter.readback("local")
        with self.assertRaisesRegex(ValueError, "rollback"):
            registry.rollback(VERSION, "rollback", "local", expected_generation=3)
        self.assertIsNone(registry.state("local")["pending"])
        self.assertEqual(adapter.readback("local"), observed)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (3,)
        )

    def test_rollback_cannot_recreate_approval_removed_while_verifying(self) -> None:
        registry, first, approval = self.candidate()
        adapter = self.adapter()
        registry.begin(VERSION, "first", first, approval, expected_generation=0)
        self.assertEqual(registry.load(VERSION, "first", adapter), "active")
        _, second, grant = self.candidate("two")
        registry.begin(VERSION, "second", second, grant, expected_generation=1)
        self.assertEqual(registry.load(VERSION, "second", adapter), "active")
        approval_key = digest(pack(approval))

        def remove_approval(value: Approval) -> str:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    "DELETE FROM objects WHERE kind='approval' AND identity=?",
                    (approval_key,),
                )
            return value.actor

        registry.verify_approval = remove_approval
        with self.assertRaisesRegex(ValueError, "unavailable or corrupt"):
            registry.rollback(VERSION, "rollback", "local", expected_generation=2)
        self.assertIsNone(registry.state("local")["pending"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM objects WHERE kind='approval' AND identity=?",
                    (approval_key,),
                ).fetchone()
            )
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (2,)
        )
