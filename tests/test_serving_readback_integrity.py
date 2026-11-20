"""Readback is observation, not authority to rewrite another activation."""

import importlib
import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from refinery import serving
from refinery.storage import VERSION

fixtures = importlib.import_module("test_durable")
ROOT = Path(__file__).resolve().parents[1]


class ReadbackIntegrityTests(unittest.TestCase):
    setUp: Any = fixtures.DurableBase.setUp
    candidate: Any = fixtures.ServingTests.candidate
    adapter: Any = fixtures.ServingTests.adapter
    store: Any
    directory: Path

    def pending(self) -> tuple[Any, Any, dict[str, Any]]:
        registry, key, approval = self.candidate()
        adapter = self.adapter()
        registry.begin(VERSION, "activation", key, approval, expected_generation=0)
        expected = {
            "activation_id": "activation",
            "generation": 1,
            "candidate_sha256": approval.candidate_sha256,
        }
        return registry, adapter, expected

    def claim_without_loading(self, registry: Any, adapter: Any) -> None:
        def stop(point: str) -> None:
            if point == "before_candidate_load":
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            registry.load(VERSION, "activation", adapter, checkpoint=stop)

    def observe(self, registry: Any, value: Any, *, rejected: bool = False) -> str:
        class ReadOnly:
            def load(self, *args: Any) -> None:
                raise AssertionError("reconciliation must not load")

            def readback(self, environment: str) -> Any:
                if environment != "local":
                    raise AssertionError("wrong target requested")
                return value

        with patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        ) as network:
            result = (
                registry._reconcile(VERSION, "activation", ReadOnly(), rejected=True)
                if rejected
                else registry.reconcile(VERSION, "activation", ReadOnly())
            )
            network.assert_not_called()
        return cast(str, result)

    def test_foreign_equality_and_boolean_generation_never_activate(self) -> None:
        registry, adapter, expected = self.pending()
        self.claim_without_loading(registry, adapter)

        class Foreign:
            def __eq__(self, other: Any) -> bool:
                raise AssertionError("foreign equality invoked")

        for value in (
            Foreign(),
            {**expected, "generation": True},
            {**expected, "generation": 1.0},
            {**expected, "activation_id": Foreign()},
            {**expected, "candidate_sha256": Foreign()},
            {**expected, "activation_id": "other-target-activation"},
            {**expected, "candidate_sha256": "0" * 64},
            {**expected, "environment": "other-target"},
        ):
            with self.subTest(value=type(value).__name__):
                self.assertEqual(self.observe(registry, value), "unknown")
                self.assertIsNone(registry.state("local")["active"])
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )
        self.assertIsNone(adapter.readback("local"))

    def test_readback_callback_mutation_cannot_commit_stale_observation(self) -> None:
        registry, adapter, expected = self.pending()
        self.claim_without_loading(registry, adapter)
        for sql in (
            "UPDATE environments SET generation=2 WHERE identity='local'",
            "UPDATE activations SET generation=2 WHERE identity='activation'",
            "UPDATE activations SET state='intent' WHERE identity='activation'",
        ):
            with self.subTest(sql=sql):

                class Mutating:
                    def readback(inner, environment: str) -> Any:
                        self.store.db.execute(sql)
                        return expected

                with self.assertRaisesRegex(ValueError, "changed during readback"):
                    registry.reconcile(VERSION, "activation", Mutating())
                self.assertIsNone(registry.state("local")["active"])
                self.store.db.execute("UPDATE environments SET generation=1")
                self.store.db.execute(
                    "UPDATE activations SET generation=1,state='unknown'"
                )
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

    def test_rejection_requires_exact_previous_activation_not_only_digest(self) -> None:
        registry, first, grant = self.candidate()
        adapter = self.adapter()
        registry.begin(VERSION, "first", first, grant, expected_generation=0)
        self.assertEqual(registry.load(VERSION, "first", adapter), "active")
        prior = adapter.readback("local")
        _, second, approval = self.candidate("two")
        registry.begin(VERSION, "activation", second, approval, expected_generation=1)
        self.claim_without_loading(registry, adapter)
        for value in (
            {"candidate_sha256": prior["candidate_sha256"]},
            {**prior, "activation_id": "foreign"},
            {**prior, "generation": True},
            {**prior, "generation": 0},
            {**prior, "extra": "untrusted"},
        ):
            self.assertEqual(self.observe(registry, value, rejected=True), "unknown")
            self.assertEqual(registry.state("local")["pending"], "activation")
        self.assertEqual(self.observe(registry, prior, rejected=True), "failed")
        self.assertEqual(registry.state("local")["active"], first)
        self.assertEqual(adapter.predict("local", "hello")["output"], "answerone")
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 1
        )

    def test_lost_ack_fresh_process_reconciles_exact_bytes_without_reload(self) -> None:
        registry, adapter, _ = self.pending()

        def stop(point: str) -> None:
            if point == "after_candidate_load":
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            registry.load(VERSION, "activation", adapter, checkpoint=stop)
        code = """
import json, socket, sys
from pathlib import Path
from unittest.mock import patch
from refinery.serving import Registry, LocalServing
from refinery.storage import Store, VERSION
root = Path(sys.argv[1])
with Store(root/'refinery.sqlite') as store:
 adapter = LocalServing(root/'serving.sqlite')
 try:
  with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')) as network:
   with patch.object(adapter, 'load', side_effect=AssertionError('reload forbidden')) as load:
    state = Registry(store).load(VERSION, 'activation', adapter)
    output = adapter.predict('local', 'hello')
    count = adapter.db.execute('SELECT count(*) FROM calls').fetchone()[0]
    load.assert_not_called(); network.assert_not_called()
    print(json.dumps({'state':state,'output':output,'calls':count}))
 finally: adapter.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.directory)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(
            receipt,
            {
                "state": "active",
                "calls": 1,
                "output": adapter.predict("local", "hello"),
            },
        )
        self.assertEqual(receipt["output"]["output"], "answerone")

    def test_unclaimed_intent_cannot_adopt_matching_loaded_bytes(self) -> None:
        registry, adapter, expected = self.pending()
        candidate_key = self.store.db.execute(
            "SELECT candidate FROM activations WHERE identity='activation'"
        ).fetchone()[0]
        candidate = self.store.get("candidate", candidate_key)
        adapter.load(
            "activation",
            "local",
            1,
            expected["candidate_sha256"],
            candidate["content"],
            None,
        )
        self.assertEqual(adapter.readback("local"), expected)
        with patch.object(adapter, "readback", wraps=adapter.readback) as readback:
            self.assertEqual(
                registry.reconcile(VERSION, "activation", adapter), "intent"
            )
            readback.assert_not_called()
        self.assertEqual(
            registry.state("local"),
            {
                "generation": 1,
                "active": None,
                "pending": "activation",
            },
        )
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 1
        )

    def test_caller_rejection_cannot_release_claimed_reservation(self) -> None:
        registry, adapter, _ = self.pending()
        self.claim_without_loading(registry, adapter)
        self.assertIsNone(adapter.readback("local"))
        with patch.object(adapter, "readback", wraps=adapter.readback) as readback:
            with self.assertRaisesRegex(ValueError, "dispatching load"):
                registry.reconcile(VERSION, "activation", adapter, rejected=True)
            readback.assert_not_called()
        self.assertEqual(registry.reconcile(VERSION, "activation", adapter), "unknown")
        self.assertEqual(registry.state("local")["pending"], "activation")
        self.assertIsNone(registry.state("local")["active"])
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

    def test_removed_strict_readback_guard_is_detected(self) -> None:
        with patch.object(
            serving,
            "_readback_matches",
            lambda observed, expected: observed == expected,
        ):
            result = unittest.TestResult()
            ReadbackIntegrityTests(
                "test_foreign_equality_and_boolean_generation_never_activate"
            ).run(result)
        self.assertTrue(
            result.errors or result.failures,
            "loose equality escaped negative regression",
        )


if __name__ == "__main__":
    unittest.main()
