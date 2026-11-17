"""Current readback is separate from immutable historical serving outcomes."""

import importlib
import inspect
import json
import socket
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import serving
from refinery.lineage import digest
from refinery.storage import VERSION

fixtures = importlib.import_module("test_durable")
ROOT = Path(__file__).resolve().parents[1]
OTHER = b'{"format":"synthetic.map.v1","responses":{"hello":"drifted"}}'


class ServingObservationTests(unittest.TestCase):
    setUp: Any = fixtures.DurableBase.setUp
    candidate: Any = fixtures.ServingTests.candidate
    adapter: Any = fixtures.ServingTests.adapter
    store: Any
    directory: Path

    def active(self) -> tuple[Any, Any, str]:
        registry, key, grant = self.candidate()
        adapter = self.adapter()
        registry.begin(VERSION, "first", key, grant, expected_generation=0)
        self.assertEqual(registry.load(VERSION, "first", adapter), "active")
        return registry, adapter, key

    def observe(self, registry: Any, adapter: Any, status: str) -> dict[str, Any]:
        before = tuple(self.store.db.iterdump())
        with (
            patch.object(
                socket, "socket", side_effect=AssertionError("network forbidden")
            ) as network,
            patch.object(
                adapter, "load", side_effect=AssertionError("load forbidden")
            ) as load,
        ):
            result = registry.observe(VERSION, "local", adapter)
            network.assert_not_called()
            load.assert_not_called()
        self.assertEqual(result["version"], "runspindle.serving-observation.v1")
        self.assertEqual(result["environment"], "local")
        self.assertEqual(result["status"], status)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        return dict(result)

    def test_empty_current_and_drift_are_actual_readback_not_historical_status(
        self,
    ) -> None:
        registry = serving.Registry(self.store)
        adapter = self.adapter()
        self.assertEqual(self.observe(registry, adapter, "empty")["generation"], 0)
        registry, key, grant = self.candidate()
        registry.begin(VERSION, "first", key, grant, expected_generation=0)
        registry.load(VERSION, "first", adapter)
        current = self.observe(registry, adapter, "current")
        self.assertEqual(current["active_candidate"], key)
        self.assertEqual(
            current["candidate_sha256"], current["observed_candidate_sha256"]
        )
        adapter.load(
            "outside-registry", "local", 2, digest(OTHER), OTHER, grant.candidate_sha256
        )
        changed = self.observe(registry, adapter, "drift")
        self.assertEqual(changed["observed_candidate_sha256"], digest(OTHER))
        self.assertEqual(registry.reconcile(VERSION, "first", adapter), "active")
        self.assertEqual(adapter.predict("local", "hello")["output"], "drifted")
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (2,)
        )

    def test_pending_never_claims_current_even_after_lost_acknowledgment(self) -> None:
        registry, adapter, _ = self.active()
        _, second, grant = self.candidate("two")
        registry.begin(VERSION, "second", second, grant, expected_generation=1)
        pending = self.observe(registry, adapter, "pending")
        self.assertEqual(pending["pending_activation"], "second")

        def stop(point: str) -> None:
            if point == "after_candidate_load":
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            registry.load(VERSION, "second", adapter, checkpoint=stop)
        pending = self.observe(registry, adapter, "pending")
        self.assertEqual(pending["observed_candidate_sha256"], grant.candidate_sha256)
        self.assertNotEqual(
            pending["candidate_sha256"], pending["observed_candidate_sha256"]
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT state FROM activations WHERE identity='second'"
            ).fetchone(),
            ("unknown",),
        )

    def test_unavailable_and_malformed_readback_never_discloses_raw_payload(
        self,
    ) -> None:
        registry, adapter, _ = self.active()
        foreign = object()
        expected = adapter.readback("local")
        for observed in (
            foreign,
            {**expected, "generation": True},
            {**expected, "activation_id": "bad identity"},
            {**expected, "candidate_sha256": "private-canary"},
            {**expected, "extra": "private-canary"},
        ):
            with (
                self.subTest(observed=type(observed).__name__),
                patch.object(adapter, "readback", return_value=observed),
            ):
                result = self.observe(registry, adapter, "unavailable")
                self.assertIsNone(result["observed_candidate_sha256"])
                self.assertNotIn("private-canary", json.dumps(result))
        with patch.object(
            adapter, "readback", side_effect=RuntimeError("private-canary")
        ):
            result = self.observe(registry, adapter, "unavailable")
            self.assertNotIn("private-canary", json.dumps(result))

    def test_same_digest_wrong_activation_or_generation_is_drift(self) -> None:
        registry, adapter, _ = self.active()
        expected = adapter.readback("local")
        for observed in (
            None,
            {**expected, "activation_id": "other"},
            {**expected, "generation": 2},
        ):
            with (
                self.subTest(observed=observed),
                patch.object(adapter, "readback", return_value=observed),
            ):
                self.observe(registry, adapter, "drift")

    def test_invalid_registry_binding_rejects_before_readback(self) -> None:
        registry, adapter, _ = self.active()
        self.store.db.execute(
            "UPDATE activations SET generation=2 WHERE identity='first'"
        )
        before = tuple(self.store.db.iterdump())
        with patch.object(
            adapter, "readback", side_effect=AssertionError("readback forbidden")
        ) as read:
            with self.assertRaises(ValueError):
                registry.observe(VERSION, "local", adapter)
            with self.assertRaises(ValueError):
                registry.observe("unsupported", "local", adapter)
            read.assert_not_called()
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_readback_interruption_propagates_without_registry_writes(self) -> None:
        registry, adapter, _ = self.active()
        before = tuple(self.store.db.iterdump())
        with patch.object(adapter, "readback", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                registry.observe(VERSION, "local", adapter)
        self.assertEqual(tuple(self.store.db.iterdump()), before)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (1,)
        )

    def test_callback_registry_change_rejects_stale_observation(self) -> None:
        registry, adapter, _ = self.active()
        actual = adapter.readback("local")
        for statement in (
            "UPDATE environments SET generation=2",
            "UPDATE activations SET generation=2 WHERE identity='first'",
        ):

            def changed(environment: str) -> Any:
                self.store.db.execute(statement)
                return actual

            with (
                patch.object(adapter, "readback", side_effect=changed),
                self.assertRaises(ValueError),
            ):
                registry.observe(VERSION, "local", adapter)
            self.assertEqual(
                adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (1,)
            )
            self.store.db.execute("UPDATE environments SET generation=1")
            self.store.db.execute(
                "UPDATE activations SET generation=1 WHERE identity='first'"
            )

    def test_fresh_process_observes_drift_without_load_or_history_rewrite(self) -> None:
        registry, adapter, _ = self.active()
        old = adapter.readback("local")["candidate_sha256"]
        adapter.load("outside", "local", 2, digest(OTHER), OTHER, old)
        before = tuple(self.store.db.iterdump())
        code = """
import json,socket,sys
from pathlib import Path
from unittest.mock import patch
from refinery.storage import Store,VERSION
from refinery.serving import Registry,LocalServing
root=Path(sys.argv[1])
with Store(root/'refinery.sqlite') as store:
 adapter=LocalServing(root/'serving.sqlite')
 try:
  with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')) as network, patch.object(adapter,'load',side_effect=AssertionError('load forbidden')) as load:
   observed=Registry(store).observe(VERSION,'local',adapter)
   network.assert_not_called();load.assert_not_called()
   print(json.dumps({'observation':observed,'calls':adapter.db.execute('SELECT count(*) FROM calls').fetchone()[0]}))
 finally:adapter.close()
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.directory)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["observation"]["status"], "drift")
        self.assertEqual(
            receipt["observation"]["observed_candidate_sha256"], digest(OTHER)
        )
        self.assertEqual(receipt["calls"], 2)
        self.assertEqual(tuple(self.store.db.iterdump()), before)

    def test_removed_actual_match_makes_drift_falsely_current(self) -> None:
        registry, adapter, _ = self.active()
        old = adapter.readback("local")["candidate_sha256"]
        adapter.load("outside", "local", 2, digest(OTHER), OTHER, old)
        source = textwrap.dedent(inspect.getsource(serving.Registry.observe))
        guard = "expected is not None and _readback_matches(observed, expected)"
        self.assertEqual(source.count(guard), 1)
        scope = dict(vars(serving))
        exec(
            compile(
                source.replace(guard, "expected is not None"),
                "<observation-mutation>",
                "exec",
            ),
            scope,
        )
        self.observe(registry, adapter, "drift")
        with patch.object(serving.Registry, "observe", scope["observe"]):
            with self.assertRaises(AssertionError):
                self.observe(registry, adapter, "drift")
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone(), (2,)
        )


if __name__ == "__main__":
    unittest.main()
