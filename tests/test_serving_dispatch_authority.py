"""Actual disposable registry/loader; synthetic approval authority only."""

import importlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery.serving import Registry
from refinery.storage import VERSION, pack

fixtures = importlib.import_module("test_durable")
ROOT = Path(__file__).resolve().parents[1]


class DispatchAuthorityTests(unittest.TestCase):
    setUp: Any = fixtures.DurableBase.setUp
    candidate: Any = fixtures.ServingTests.candidate
    adapter: Any = fixtures.ServingTests.adapter
    store: Any
    directory: Path
    path: Path

    def pending(self) -> tuple[Any, ...]:
        registry, key, approval = self.candidate()
        adapter = self.adapter()
        registry.begin(VERSION, "activation", key, approval, expected_generation=0)
        return registry, key, approval, adapter

    def test_revocation_before_dispatch_denies_without_state_or_load_effect(self) -> None:
        registry, _, _, adapter = self.pending()
        before = tuple(self.store.db.iterdump())
        registry.verify_approval = lambda approval: None
        with self.assertRaisesRegex(ValueError, "approval denied"):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)
        self.assertIsNone(adapter.readback("local"))

    def test_checkpoint_revocation_keeps_unknown_without_retry(self) -> None:
        registry, _, _, adapter = self.pending()
        def checkpoint(point: str) -> None:
            if point == "before_candidate_load":
                registry.verify_approval = lambda approval: None
        with self.assertRaisesRegex(ValueError, "approval denied"):
            registry.load(VERSION, "activation", adapter, checkpoint=checkpoint)
        self.assertEqual(registry._intent("activation")[-1], "unknown")
        registry.verify_approval = lambda approval: approval.actor
        self.assertEqual(registry.load(VERSION, "activation", adapter), "unknown")
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_callback_ownership_change_denies_dispatch(self) -> None:
        registry, _, _, adapter = self.pending()
        def authority(approval: Any) -> str:
            with self.store.transaction() as tx:
                tx.execute("UPDATE environments SET generation=generation+1 WHERE identity='local'")
            return str(approval.actor)
        registry.verify_approval = authority
        with self.assertRaisesRegex(ValueError, "binding changed"):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_approval_pointer_substitution_fails_closed(self) -> None:
        registry, _, approval, adapter = self.pending()
        with self.store.transaction() as tx:
            tx.execute("UPDATE activations SET approval=? WHERE identity='activation'", ("0" * 64,))
        with self.assertRaises(ValueError):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_callback_mutated_approval_cannot_change_binding(self) -> None:
        registry, _, approval, adapter = self.pending()
        original = pack(approval)
        def authority(value: Any) -> str:
            object.__setattr__(value, "environment", "other")
            return str(value.actor)
        registry.verify_approval = authority
        with self.assertRaisesRegex(ValueError, "approval denied"):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(pack(approval), original)
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_missing_raising_and_nonidentity_authority_deny(self) -> None:
        registry, _, _, adapter = self.pending()
        def unavailable(approval: Any) -> None:
            raise RuntimeError("synthetic-secret-canary")
        for authority in (None, lambda a: True, unavailable):
            registry.verify_approval = authority
            with self.assertRaisesRegex(ValueError, "^activation approval denied$"):
                registry.load(VERSION, "activation", adapter)
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_foreign_identity_comparison_is_not_invoked(self) -> None:
        registry, _, _, adapter = self.pending()
        class ForeignIdentity:
            def __eq__(self, other: Any) -> bool:
                raise AssertionError("foreign identity comparison")
            def __ne__(self, other: Any) -> bool:
                raise AssertionError("foreign identity comparison")
        registry.verify_approval = lambda approval: ForeignIdentity()
        with self.assertRaisesRegex(ValueError, "approval denied"):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_authority_cancellation_before_and_after_claim_never_loads(self) -> None:
        registry, _, _, adapter = self.pending()
        before = tuple(self.store.db.iterdump())
        def cancel(approval: Any) -> None:
            raise KeyboardInterrupt()
        registry.verify_approval = cancel
        with self.assertRaises(KeyboardInterrupt):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        calls = 0
        def later(approval: Any) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return str(approval.actor)
        registry.verify_approval = later
        with self.assertRaises(KeyboardInterrupt):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(registry._intent("activation")[-1], "unknown")
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_fresh_process_requires_current_authority(self) -> None:
        self.pending()
        code = '''
import json, socket, sys
from pathlib import Path
from unittest.mock import patch
from refinery.storage import Store, VERSION
from refinery.serving import Registry, LocalServing
root=Path(sys.argv[1])
store=Store(root/'refinery.sqlite'); adapter=LocalServing(root/'serving.sqlite')
try:
 with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')) as sockets:
  before=tuple(store.db.iterdump())
  try: Registry(store).load(VERSION,'activation',adapter)
  except ValueError: pass
  else: raise RuntimeError('stale authority dispatched')
  if tuple(store.db.iterdump()) != before: raise RuntimeError('unexpected state write')
  count=adapter.db.execute('SELECT count(*) FROM calls').fetchone()[0]
  if count != 0: raise RuntimeError('unexpected load')
  sockets.assert_not_called()
  print(json.dumps({'calls':count}))
finally: adapter.close(); store.close()
'''
        result = subprocess.run([sys.executable, "-O", "-c", code, str(self.directory)],
                                cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"calls": 0})

    def test_lost_ack_reconciliation_stays_read_only_after_revocation(self) -> None:
        registry, _, _, adapter = self.pending()
        class LostAck:
            def load(self, *args: Any) -> None:
                adapter.load(*args)
                registry.verify_approval = None
                raise OSError("synthetic acknowledgment loss")
            def readback(self, environment: str) -> Any:
                return adapter.readback(environment)
        self.assertEqual(registry.load(VERSION, "activation", LostAck()), "active")
        self.assertEqual(registry.load(VERSION, "activation", adapter), "active")
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 1)
        self.assertEqual(adapter.predict("local", "hello")["output"], "answerone")

    def test_revoked_rollback_does_not_replace_current_loaded_bytes(self) -> None:
        registry, key, approval, adapter = self.pending()
        registry.load(VERSION, "activation", adapter)
        _, second, grant = self.candidate("two")
        registry.begin(VERSION, "second", second, grant, expected_generation=1)
        registry.load(VERSION, "second", adapter)
        registry.rollback(VERSION, "rollback", "local", expected_generation=2)
        registry.verify_approval = None
        with self.assertRaisesRegex(ValueError, "approval denied"):
            registry.load(VERSION, "rollback", adapter)
        self.assertEqual(adapter.predict("local", "hello")["output"], "answertwo")
        self.assertEqual(adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 2)

    def test_removed_dispatch_guard_mutation_fails_negative(self) -> None:
        def bypass(registry: Any, activation: str, expected: Any) -> Any:
            return registry.store.get("candidate", expected[2]), None
        with patch.object(Registry, "_authorize_load", bypass):
            result = unittest.TestResult()
            DispatchAuthorityTests("test_revocation_before_dispatch_denies_without_state_or_load_effect").run(result)
        self.assertEqual(result.errors, [], repr(result.errors))
        self.assertTrue(result.failures, "removed dispatch authority escaped test")
