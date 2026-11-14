"""Actual SQLite/process/dispatch observations for persisted synthetic selection."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any
import unittest
from unittest.mock import patch

from refinery import evaluation as ev
from refinery.durable_evaluation import SQLiteBudget
from refinery.durable_optimization import run
from refinery.storage import VERSION
from refinery.lineage import digest

storage: Any = importlib.import_module("refinery.storage")

legacy: Any = ev
fixture: Any = importlib.import_module("test_durable").setup
ROOT = Path(__file__).resolve().parents[1]
LIMITS = ev.Limits(200, 2000, 2000, 2000)


def invoke(path: str, checkpoint: Any = lambda _: None, **changes: Any) -> Any:
    with storage.Store(path) as store:
        snapshot, _, _ = fixture(store)
        data, split = store.lineage(snapshot)
        train = [
            e for e in data.examples if split.partition(e.source.family_id) == "train"
        ]
        provider = legacy.ScriptedProvider(
            tuple(ev.Completion(e.gold, 1, 1, True, 1) for e in train),
            clock=legacy.VirtualClock(),
        )
        args = dict(
            provider=provider,
            limits=LIMITS,
            per_call=ev.Reservation(10, 10),
            budget_id="shared",
            checkpoint=checkpoint,
        )
        args.update(changes)
        result = run(store, VERSION, "search", snapshot, (b"fixed",), **args)
        return result, provider.calls, train, split


class DurableSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "store.sqlite")
        deny = patch.object(socket, "socket", side_effect=AssertionError("network"))
        spy = deny.start()
        self.addCleanup(deny.stop)
        self.addCleanup(spy.assert_not_called)

    def child(self, phase: str) -> subprocess.CompletedProcess[str]:
        script = """
import os, socket, sys
from unittest.mock import patch
from test_durable_optimization import invoke
from refinery import storage
phase = sys.argv[2]
def checkpoint(point):
    if point == phase:
        os._exit(73)
with patch.object(socket, "socket", side_effect=AssertionError("network")):
    result, calls, _, _ = invoke(sys.argv[1], checkpoint)
print(storage.pack(result).hex())
print(len(calls))
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "tests"))
        return subprocess.run(
            [sys.executable, "-c", script, self.path, phase],
            env=env,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )

    def test_completed_fresh_process_returns_identical_bytes_zero_calls(self) -> None:
        result, calls, train, split = invoke(self.path)
        self.assertEqual(result.status, "selected")
        self.assertEqual(len(calls), len(train))
        self.assertEqual(
            {r.output_id for r in calls}, {e.output.output_id for e in train}
        )
        self.assertTrue(
            all(split.partition(e.source.family_id) == "train" for e in train)
        )
        fresh = self.child("never")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(fresh.stdout.splitlines(), [storage.pack(result).hex(), "0"])

    def test_process_interruption_retains_reservation_and_never_replays(self) -> None:
        killed = self.child("after_budget_reservation")
        self.assertEqual(killed.returncode, 73, killed.stderr)
        with storage.Store(self.path) as store:
            request, outcome = store.db.execute(
                "SELECT request,outcome FROM attempts"
            ).fetchone()
            self.assertEqual(storage.unpack(request).stage, "optimization")
            self.assertEqual(outcome, "unknown")
            ledger: Any = SQLiteBudget(
                store, "shared", LIMITS, clock_ms=legacy.VirtualClock()
            )
            self.assertEqual(ledger.snapshot()["calls"], 1)
            self.assertEqual(ledger.snapshot()["unresolved_calls"], 1)
        with patch.object(
            ev.ScriptedProvider, "complete", side_effect=AssertionError("replay")
        ) as spy:
            with self.assertRaisesRegex(ev.Halt, "interrupted_or_owned_selection"):
                invoke(self.path)
            spy.assert_not_called()

    def test_interruption_before_and_after_result_commit(self) -> None:
        for phase in ("before_selection_commit", "after_selection_commit"):
            with self.subTest(phase=phase):
                self.path += ".next"
                killed = self.child(phase)
                self.assertEqual(killed.returncode, 73, killed.stderr)
                with patch.object(
                    ev.ScriptedProvider,
                    "complete",
                    side_effect=AssertionError("replay"),
                ) as spy:
                    if phase.startswith("before"):
                        with self.assertRaises(ev.Halt):
                            invoke(self.path)
                    else:
                        result, calls, _, _ = invoke(self.path)
                        self.assertEqual(result.status, "selected")
                        self.assertEqual(calls, [])
                    spy.assert_not_called()

    def test_concurrent_owner_blocks_loser_before_dispatch(self) -> None:
        # Seed schema/lineage before racing claims; separate connections per thread.
        with storage.Store(self.path) as store:
            fixture(store)
        claimed, release = threading.Event(), threading.Event()

        def checkpoint(point: str) -> None:
            if point == "after_selection_claim":
                claimed.set()
                if not release.wait(10):
                    raise TimeoutError("test release")

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke, self.path, checkpoint)
            self.assertTrue(claimed.wait(10))
            try:
                with patch.object(
                    ev.ScriptedProvider,
                    "complete",
                    side_effect=AssertionError("loser dispatch"),
                ) as spy:
                    with self.assertRaises(ev.Halt):
                        invoke(self.path)
                    spy.assert_not_called()
            finally:
                release.set()
            result, calls, train, _ = first.result(timeout=10)
        self.assertEqual(result.status, "selected")
        self.assertEqual(len(calls), len(train))

    def test_binding_changes_and_malformed_inputs_have_no_calls(self) -> None:
        invoke(self.path)
        with patch.object(
            ev.ScriptedProvider, "complete", side_effect=AssertionError("dispatch")
        ) as spy:
            for changes in (
                {"budget_id": "different"},
                {"max_attempts": 2},
                {"limits": ev.Limits(201, 2000, 2000, 2000)},
                {"per_call": ev.Reservation(11, 10)},
            ):
                with (
                    self.subTest(changes=changes),
                    self.assertRaisesRegex(ValueError, "immutable selection conflict"),
                ):
                    invoke(self.path, **changes)
            with self.assertRaises(ValueError):
                invoke(self.path, max_attempts=0)
            spy.assert_not_called()

    def test_missing_claim_guard_mutation_is_detected(self) -> None:
        # Deliberately replace ownership denial with deleting the claim.
        # Independent request observation detects forbidden replay.
        source = (ROOT / "refinery/durable_optimization.py").read_text()
        anchor = 'raise ev.Halt("interrupted_or_owned_selection")'
        self.assertEqual(source.count(anchor), 1)
        killed = self.child("after_selection_claim")
        self.assertEqual(killed.returncode, 73, killed.stderr)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutant.py"
            path.write_text(
                source.replace(
                    anchor,
                    'tx.execute("DELETE FROM experiments WHERE identity=?", (key,))',
                )
            )
            spec = importlib.util.spec_from_file_location(
                "refinery.mutant_durable", path
            )
            if spec is None or spec.loader is None:
                self.fail("missing mutation loader")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with patch(__name__ + ".run", module.run):
                _, calls, _, _ = invoke(self.path)
            self.assertGreater(len(calls), 0, "mutant must dispatch forbidden replay")

    def test_cached_misassociated_results_fail_without_dispatch(self) -> None:
        original, _, _, _ = invoke(self.path)
        mutations = (
            {"configuration_sha256": "0" * 64},
            {"dataset_sha256": "0" * 64},
            {"split_sha256": "0" * 64},
            {"provider_script_sha256": "0" * 64},
            {"selected": b"foreign", "selected_sha256": digest(b"foreign")},
            {"selected_sha256": "0" * 64},
            {"status": "blocked"},
            {"training_cases": ()},
            {"candidate_scores": ((digest(b"fixed"), -1),)},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                bad = replace(original, **changes)
                with storage.Store(self.path) as store:
                    sha = digest(storage.pack(bad))
                    with store.transaction() as tx:
                        store.put(tx, "selection", sha, bad)
                        tx.execute("UPDATE experiments SET report=?", (sha,))
                with patch.object(
                    ev.ScriptedProvider,
                    "complete",
                    side_effect=AssertionError("dispatch"),
                ) as spy:
                    with self.assertRaises(ValueError):
                        invoke(self.path)
                    spy.assert_not_called()

    def test_new_claim_rejects_consumed_provider_without_writes(self) -> None:
        with storage.Store(self.path) as store:
            snapshot, _, _ = fixture(store)
            data, split = store.lineage(snapshot)
            train = [
                e
                for e in data.examples
                if split.partition(e.source.family_id) == "train"
            ]
            provider = legacy.ScriptedProvider(
                tuple(ev.Completion(e.gold, 1, 1, True, 1) for e in train),
                clock=legacy.VirtualClock(),
            )
            provider.complete(
                ev.Request(
                    "optimization", "already", "v1", "0" * 64, "0" * 64, "0" * 64, 1
                )
            )
            with self.assertRaisesRegex(ValueError, "pristine provider cursor"):
                run(
                    store,
                    VERSION,
                    "search",
                    snapshot,
                    (b"fixed",),
                    provider=provider,
                    limits=LIMITS,
                    per_call=ev.Reservation(10, 10),
                    budget_id="shared",
                )
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(
                store.db.execute("SELECT count(*) FROM experiments").fetchone()[0], 0
            )
            self.assertEqual(
                store.db.execute("SELECT count(*) FROM budgets").fetchone()[0], 0
            )
