"""Real shared SQLite ledgers with synthetic requests and independent row checks."""

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import durable_evaluation as durable
from refinery.durable_evaluation import SQLiteBudget
from refinery.evaluation import Attempt, Halt, Limits, Request, Reservation
from refinery.storage import Store, pack, unpack

ROOT = Path(__file__).resolve().parents[1]
LIMITS = Limits(10, 10, 1000, 10)
REQUEST = Request("optimization", "output", "1", "a" * 64, "b" * 64, "c" * 64, 1)
RECEIPT = Attempt(REQUEST, "completed", "d" * 64, 0, 0, True, 0)


class SettlementOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "refinery.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        network = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        self.network = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(self.network.assert_not_called)

    def budget(self, owner: str | None, identity: str = "shared") -> SQLiteBudget:
        return SQLiteBudget(
            self.store, identity, LIMITS, clock_ms=lambda: 0, experiment=owner
        )

    def state(self, identity: str = "shared") -> Any:
        return unpack(
            self.store.db.execute(
                "SELECT payload FROM budgets WHERE identity=?", (identity,)
            ).fetchone()[0]
        )

    def test_other_experiment_and_ownerless_alias_cannot_refund(self) -> None:
        owner = self.budget("a")
        foreign, ownerless = self.budget("b"), self.budget(None)
        ticket = owner.reserve(Reservation(10, 10), request=REQUEST)
        before = tuple(self.store.db.iterdump())
        for alias in (foreign, ownerless):
            with self.assertRaisesRegex(ValueError, "ownership conflict"):
                alias.settle(ticket, tokens=0, cost_microunits=0, receipt=RECEIPT)
            self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(self.state()["pending"], {"1": Reservation(10, 10)})
        self.assertEqual(
            self.store.db.execute(
                "SELECT outcome,receipt FROM attempts WHERE experiment='a'"
            ).fetchone(),
            ("unknown", None),
        )

    def test_ownerless_ownership_and_cross_budget_ticket_collisions(self) -> None:
        first, second = self.budget(None, "one"), self.budget("b", "two")
        self.assertEqual(first.reserve(Reservation(10, 10)), 1)
        self.assertEqual(second.reserve(Reservation(10, 10), request=REQUEST), 1)
        wrong = self.budget(None, "two")
        before = tuple(self.store.db.iterdump())
        with self.assertRaises(ValueError):
            wrong.settle(1, tokens=0, cost_microunits=0)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        first.settle(1, tokens=0, cost_microunits=0)
        self.assertEqual(self.state("one")["pending"], {})
        self.assertEqual(self.state("two")["pending"], {"1": Reservation(10, 10)})

    def test_owned_cross_budget_collision_cannot_use_foreign_attempt(self) -> None:
        self.budget("a", "one").reserve(Reservation(10, 10), request=REQUEST)
        self.budget("b", "two").reserve(Reservation(10, 10), request=REQUEST)
        alias = self.budget("a", "two")
        before = tuple(self.store.db.iterdump())
        with self.assertRaisesRegex(ValueError, "ownership conflict"):
            alias.settle(1, tokens=0, cost_microunits=0, receipt=RECEIPT)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_clock_cannot_redirect_owner_or_mutate_receipt(self) -> None:
        owner = self.budget("a")
        ticket = owner.reserve(Reservation(10, 10), request=REQUEST)
        before = tuple(self.store.db.iterdump())
        for change in ("owner", "receipt"):
            receipt = replace(RECEIPT)

            def changed() -> int:
                if change == "owner":
                    owner.experiment = "b"
                else:
                    object.__setattr__(receipt, "tokens", 1)
                return 0

            owner.clock_ms = changed
            with self.assertRaisesRegex(ValueError, "binding changed"):
                owner.settle(ticket, tokens=0, cost_microunits=0, receipt=receipt)
            self.assertEqual(tuple(self.store.db.iterdump()), before)
            owner.experiment = "a"

    def test_attempt_request_and_receipt_usage_must_match(self) -> None:
        owner = self.budget("a")
        ticket = owner.reserve(Reservation(10, 10), request=REQUEST)
        before = tuple(self.store.db.iterdump())
        for receipt in (
            None,
            replace(RECEIPT, request=replace(REQUEST, attempt=2)),
            replace(RECEIPT, tokens=1),
        ):
            with self.assertRaisesRegex(ValueError, "attempt conflict"):
                owner.settle(ticket, tokens=0, cost_microunits=0, receipt=receipt)
            self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.store.db.execute(
            "UPDATE attempts SET request=?", (pack(replace(REQUEST, attempt=2)),)
        )
        before = tuple(self.store.db.iterdump())
        with self.assertRaises(ValueError):
            owner.settle(ticket, tokens=0, cost_microunits=0, receipt=RECEIPT)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_legacy_pending_without_proof_stays_charged(self) -> None:
        owner = self.budget(None)
        ticket = owner.reserve(Reservation(5, 5))
        legacy = self.state()
        del legacy["owners"]
        self.store.db.execute("UPDATE budgets SET payload=?", (pack(legacy),))
        before = tuple(self.store.db.iterdump())
        with self.assertRaises(ValueError):
            owner.settle(ticket, tokens=0, cost_microunits=0)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(owner.snapshot()["unresolved_calls"], 1)
        second = owner.reserve(Reservation(5, 5))
        owner.settle(second, tokens=0, cost_microunits=0)
        self.assertEqual(self.state()["pending"], {"1": Reservation(5, 5)})

    def test_clock_cannot_overwrite_ledger_or_settlement_binding(self) -> None:
        owner = self.budget("a")
        ticket = owner.reserve(Reservation(10, 10), request=REQUEST)
        before = tuple(self.store.db.iterdump())

        def changed() -> int:
            value = self.state()
            value["reason"] = "cancelled"
            self.store.db.execute("UPDATE budgets SET payload=?", (pack(value),))
            return 0

        owner.clock_ms = changed
        with self.assertRaisesRegex(ValueError, "binding changed"):
            owner.settle(ticket, tokens=0, cost_microunits=0, receipt=RECEIPT)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(self.state()["pending"], {"1": Reservation(10, 10)})

    def test_concurrent_owner_and_foreign_settlements_have_one_success(self) -> None:
        owner = self.budget("a")
        ticket = owner.reserve(Reservation(10, 10), request=REQUEST)

        def settle(actor: str) -> bool:
            with Store(self.path) as store:
                budget = SQLiteBudget(
                    store, "shared", LIMITS, clock_ms=lambda: 0, experiment=actor
                )
                try:
                    budget.settle(ticket, tokens=0, cost_microunits=0, receipt=RECEIPT)
                    return True
                except ValueError:
                    return False

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(settle, ("b", "a", "b", "a")))
        self.assertEqual(sum(results), 1)
        self.assertFalse(results[0] or results[2])
        self.assertEqual(self.state()["pending"], {})
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1
        )

    def test_unknown_usage_and_shared_cancel_preserve_ownership(self) -> None:
        owner = self.budget("a")
        ticket = owner.reserve(Reservation(10, 10), request=REQUEST)
        receipt = replace(RECEIPT, tokens=None, cost_microunits=None)
        with self.assertRaises(Halt):
            owner.settle(ticket, tokens=None, cost_microunits=None, receipt=receipt)
        proof = self.state()["owners"]
        self.budget(None).cancel()
        self.assertEqual(self.state()["owners"], proof)
        self.assertEqual(self.state()["pending"], {"1": Reservation(10, 10)})

    def test_fresh_process_foreign_denial_then_owner_settlement(self) -> None:
        self.budget("a").reserve(Reservation(10, 10), request=REQUEST)
        script = """
import json, socket, sys
from unittest.mock import patch
from refinery.durable_evaluation import SQLiteBudget
from refinery.evaluation import Limits
from refinery.storage import Store, unpack
with Store(sys.argv[1]) as store:
 with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')) as network:
  before=tuple(store.db.iterdump())
  other=SQLiteBudget(store,'shared',Limits(10,10,1000,10),clock_ms=lambda:0,experiment='b')
  try: other.settle(1,tokens=0,cost_microunits=0)
  except ValueError: pass
  else: raise RuntimeError('foreign settlement accepted')
  if tuple(store.db.iterdump()) != before: raise RuntimeError('state changed')
  owner=SQLiteBudget(store,'shared',Limits(10,10,1000,10),clock_ms=lambda:0,experiment='a')
  request=unpack(store.db.execute('SELECT request FROM attempts').fetchone()[0])
  from refinery.evaluation import Attempt
  owner.settle(1,tokens=0,cost_microunits=0,receipt=Attempt(request,'completed','d'*64,0,0,True,0))
  print(json.dumps(owner.snapshot()))
  network.assert_not_called()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", script, str(self.path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["unresolved_calls"], 0)

    def test_compact_proof_preserves_4096_pending_payload_capacity(self) -> None:
        owner = self.budget(None)
        value = self.state()
        value["pending"] = {str(i): Reservation(1, 1) for i in range(1, 4097)}
        value["owners"] = {
            str(i): owner._ownership_proof(i, Reservation(1, 1), None)
            for i in range(1, 4097)
        }
        value["next"] = value["calls"] = 4096
        encoded = pack(value)
        self.assertLess(len(encoded), 1_048_576)
        restored = durable._restore(unpack(encoded), lambda: 0)
        self.assertEqual(len(restored._owners), 4096)

    def test_removed_owner_check_is_detected(self) -> None:
        with patch.object(SQLiteBudget, "_settlement_owner", lambda *args: None):
            result = unittest.TestResult()
            SettlementOwnershipTests(
                "test_other_experiment_and_ownerless_alias_cannot_refund"
            ).run(result)
        self.assertFalse(result.errors, repr(result.errors))
        self.assertTrue(result.failures, "ownership bypass escaped negative test")


if __name__ == "__main__":
    unittest.main()
