"""Actual disposable registry/loader; synthetic approval authority only."""

import importlib
import inspect
import json
import subprocess
import sys
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import serving
from refinery.lineage import digest
from refinery.serving import Approval, Registry
from refinery.storage import VERSION, Store, pack

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

    def test_admission_callback_cannot_change_any_approval_binding(self) -> None:
        registry, key, original = self.candidate()
        before = tuple(self.store.db.iterdump())
        for field, changed in (
            ("environment", "other"),
            ("actor", "other-reviewer"),
            ("evidence", "different-proof"),
            ("candidate_sha256", "0" * 64),
            ("evaluation_sha256", "0" * 64),
            ("policy_sha256", "0" * 64),
        ):
            approval = replace(original)

            def authority(value: Any) -> str:
                object.__setattr__(value, field, changed)
                return str(value.actor)

            registry.verify_approval = authority
            with self.subTest(field=field), self.assertRaises(ValueError):
                registry.begin(
                    VERSION, "redirected", key, approval, expected_generation=0
                )
            self.assertEqual(tuple(self.store.db.iterdump()), before)

        script = """
import json, sqlite3, sys
connection = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)
try:
 print(json.dumps([connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                   for table in ('activations','environments')]))
finally:
 connection.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", script, str(self.path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), [0, 0])

    def test_removed_admission_snapshot_guard_detects_invalid_persistence(self) -> None:
        source = textwrap.dedent(inspect.getsource(Registry.begin))
        needle = "or pack(approval) != approval_bytes"
        self.assertEqual(source.count(needle), 1)
        namespace = dict(vars(serving))
        exec(
            compile(source.replace(needle, "or False"), "<admission-mutant>", "exec"),
            namespace,
        )
        with patch.object(Registry, "begin", namespace["begin"]):
            result = unittest.TestResult()
            DispatchAuthorityTests(
                "test_admission_callback_cannot_change_any_approval_binding"
            ).run(result)
        self.assertEqual(result.errors, [], repr(result.errors))
        self.assertTrue(result.failures, "removed admission binding guard escaped")

    def test_admission_does_not_invoke_foreign_approval_hooks(self) -> None:
        registry, key, original = self.candidate()
        before = tuple(self.store.db.iterdump())
        calls: list[str] = []

        class ForeignApproval(Approval):
            def __post_init__(self) -> None:
                calls.append("post-init")

        foreign = object.__new__(ForeignApproval)
        with self.assertRaisesRegex(ValueError, "unsupported approval type"):
            registry.begin(VERSION, "foreign", key, foreign, expected_generation=0)
        self.assertEqual(calls, [])
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_admission_ignores_shadowed_instance_validation_hook(self) -> None:
        registry, key, approval = self.candidate()
        calls: list[str] = []
        object.__setattr__(
            approval, "__post_init__", lambda: calls.append("foreign-hook")
        )
        registry.begin(VERSION, "valid", key, approval, expected_generation=0)
        self.assertEqual(calls, [])
        self.assertEqual(registry.state("local")["pending"], "valid")

    def test_admission_rechecks_retained_candidate_after_callback(self) -> None:
        registry, key, approval = self.candidate()
        after_callback: list[Any] = []

        def authority(value: Any) -> str:
            candidate = self.store.get("candidate", key)
            candidate["environment"] = "other"
            payload = pack(candidate)
            with self.store.transaction() as tx:
                trigger = tx.execute(
                    "SELECT sql FROM sqlite_master WHERE name='objects_UPDATE'"
                ).fetchone()[0]
                tx.execute("DROP TRIGGER objects_UPDATE")
                tx.execute(
                    "UPDATE objects SET digest=?,payload=? WHERE kind='candidate' AND identity=?",
                    (digest(payload), payload, key),
                )
                tx.execute(trigger)
            after_callback.extend(self.store.db.iterdump())
            return str(value.actor)

        registry.verify_approval = authority
        with self.assertRaisesRegex(ValueError, "candidate changed during approval"):
            registry.begin(VERSION, "replaced", key, approval, expected_generation=0)
        self.assertEqual(tuple(self.store.db.iterdump()), tuple(after_callback))
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM activations").fetchone(), (0,)
        )

    def test_admission_callback_cannot_replace_store_or_verifier(self) -> None:
        registry, key, approval = self.candidate()
        before = tuple(self.store.db.iterdump())
        original_store = self.store
        for change in ("store", "verifier"):

            def authority(value: Any) -> str:
                if change == "store":
                    registry.store = object()
                else:
                    registry.verify_approval = lambda approval: approval.actor
                return str(value.actor)

            registry.verify_approval = authority
            with self.assertRaisesRegex(ValueError, "approval denied"):
                registry.begin(
                    VERSION, "redirected", key, approval, expected_generation=0
                )
            registry.store = original_store
            self.assertEqual(tuple(original_store.db.iterdump()), before)

    def test_revocation_before_dispatch_denies_without_state_or_load_effect(
        self,
    ) -> None:
        registry, _, _, adapter = self.pending()
        before = tuple(self.store.db.iterdump())
        registry.verify_approval = lambda approval: None
        with self.assertRaisesRegex(ValueError, "approval denied"):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )
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
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

    def test_callback_ownership_change_denies_dispatch(self) -> None:
        registry, _, _, adapter = self.pending()

        def authority(approval: Any) -> str:
            with self.store.transaction() as tx:
                tx.execute(
                    "UPDATE environments SET generation=generation+1 WHERE identity='local'"
                )
            return str(approval.actor)

        registry.verify_approval = authority
        with self.assertRaisesRegex(ValueError, "binding changed"):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

    def _dispatch_context_substitution(self, field: str, *, call: int = 1) -> None:
        registry, _, _, adapter = self.pending()
        checks = 0
        with Store(self.path) as replacement:
            before = tuple(self.store.db.iterdump())

            def replace_context(approval: Approval) -> str:
                nonlocal checks
                checks += 1
                if checks == call:
                    if field == "store":
                        registry.store = replacement
                    else:
                        registry.verify_approval = lambda approval: approval.actor
                return approval.actor

            registry.verify_approval = replace_context
            try:
                with self.assertRaisesRegex(ValueError, "approval denied"):
                    registry.load(VERSION, "activation", adapter)
            finally:
                registry.store = self.store
                self.assertEqual(
                    adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (0,)
                )
            if call == 1:
                self.assertEqual(tuple(replacement.db.iterdump()), before)
                self.assertEqual(tuple(self.store.db.iterdump()), before)
            else:
                self.assertEqual(
                    replacement.db.execute(
                        "SELECT state FROM activations WHERE identity='activation'"
                    ).fetchone(),
                    ("unknown",),
                )
                self.assertEqual(registry.state("local")["pending"], "activation")
            self.assertEqual(checks, call)
            self.assertIsNone(adapter.readback("local"))

    def test_dispatch_rejects_store_substitution_by_verifier(self) -> None:
        self._dispatch_context_substitution("store")

    def test_dispatch_rejects_verifier_substitution_by_verifier(self) -> None:
        self._dispatch_context_substitution("verify_approval")

    def test_claimed_dispatch_rejects_context_change_in_second_verification(
        self,
    ) -> None:
        self._dispatch_context_substitution("verify_approval", call=2)

    def test_checkpoint_store_substitution_retains_unknown_without_dispatch(
        self,
    ) -> None:
        registry, _, _, adapter = self.pending()
        with Store(self.path) as replacement:

            def replace_store(point: str) -> None:
                if point == "before_candidate_load":
                    registry.store = replacement

            try:
                with self.assertRaisesRegex(ValueError, "approval denied"):
                    registry.load(
                        VERSION, "activation", adapter, checkpoint=replace_store
                    )
            finally:
                registry.store = self.store
                self.assertEqual(
                    adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (0,)
                )
            self.assertEqual(
                replacement.db.execute(
                    "SELECT state FROM activations WHERE identity='activation'"
                ).fetchone(),
                ("unknown",),
            )
        self.assertEqual(registry.load(VERSION, "activation", adapter), "unknown")
        self.assertIsNone(adapter.readback("local"))

    def test_approval_pointer_substitution_fails_closed(self) -> None:
        registry, _, approval, adapter = self.pending()
        with self.store.transaction() as tx:
            tx.execute(
                "UPDATE activations SET approval=? WHERE identity='activation'",
                ("0" * 64,),
            )
        with self.assertRaises(ValueError):
            registry.load(VERSION, "activation", adapter)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

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
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

    def test_missing_raising_and_nonidentity_authority_deny(self) -> None:
        registry, _, _, adapter = self.pending()

        def unavailable(approval: Any) -> None:
            raise RuntimeError("synthetic-secret-canary")

        for authority in (None, lambda a: True, unavailable):
            registry.verify_approval = authority
            with self.assertRaisesRegex(ValueError, "^activation approval denied$"):
                registry.load(VERSION, "activation", adapter)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

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
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

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
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

    def test_fresh_process_requires_current_authority(self) -> None:
        self.pending()
        code = """
import json, socket, sys
from pathlib import Path
from unittest.mock import patch
from refinery.storage import Store, VERSION
from refinery import serving
from refinery.lineage import digest
from refinery.serving import Approval, Registry, LocalServing
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
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.directory)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
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
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 1
        )
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
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 2
        )

    def test_removed_dispatch_guard_mutation_fails_negative(self) -> None:
        def bypass(registry: Any, activation: str, expected: Any) -> Any:
            return registry.store.get("candidate", expected[2]), None

        with patch.object(Registry, "_authorize_load", bypass):
            result = unittest.TestResult()
            DispatchAuthorityTests(
                "test_revocation_before_dispatch_denies_without_state_or_load_effect"
            ).run(result)
        self.assertEqual(result.errors, [], repr(result.errors))
        self.assertTrue(result.failures, "removed dispatch authority escaped test")
