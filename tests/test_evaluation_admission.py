"""Offline admission proofs using actual SQLite stores and observed dispatches."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

de: Any = importlib.import_module("refinery.durable_evaluation")
ev: Any = importlib.import_module("refinery.evaluation")
storage: Any = importlib.import_module("refinery.storage")
fixture: Any = importlib.import_module("test_durable").setup
ROOT = Path(__file__).resolve().parents[1]


def prefixed(store: Any) -> tuple[Any, Any, Any]:
    snapshot, experiment, provider = fixture(store)
    # A consumed, valid prefix must not select the following passing script.
    provider = ev.ScriptedProvider(
        (ev.Completion(b"wrong", 1, 1, True, 0),) + provider.script,
        clock=ev.VirtualClock(),
    )
    config = replace(experiment.configuration, provider_script_sha256=provider.sha256)
    return (
        snapshot,
        replace(experiment, configuration=config, config_sha256=config.sha256),
        provider,
    )


def consume(provider: Any) -> None:
    provider.complete(
        ev.Request("baseline", "prefix", "1", "0" * 64, "0" * 64, "0" * 64, 1)
    )


def invoke(store: Any, ready: tuple[Any, Any, Any], **kwargs: Any) -> Any:
    snapshot, experiment, provider = ready
    args = dict(provider=provider, budget_id="budget", synthetic_fixture=True)
    args.update(kwargs)
    return de.run(store, storage.VERSION, "evaluation", snapshot, experiment, **args)


def contents(store: Any) -> list[str]:
    # Independent logical-byte observation includes claims, budgets and attempts.
    return list(store.db.iterdump())


class EvaluationAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="refinery-admission-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "refinery.sqlite"
        self.store = storage.Store(self.path)
        self.addCleanup(self.store.close)
        denied = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = denied.start()
        self.addCleanup(denied.stop)
        self.addCleanup(spy.assert_not_called)

    def test_wrong_provider_configuration_changes_no_rows_and_retry_succeeds(
        self,
    ) -> None:
        ready = fixture(self.store)
        wrong = ev.ScriptedProvider(
            (ev.Completion(b"wrong", 1, 1, True),), clock=ev.VirtualClock()
        )
        before = contents(self.store)
        with self.assertRaisesRegex(ValueError, "adapter configuration changed"):
            invoke(self.store, ready, provider=wrong)
        self.assertEqual(contents(self.store), before)
        self.assertEqual(wrong.calls, [])
        with storage.Store(self.path) as fresh:
            self.assertEqual(invoke(fresh, ready).status, "passed")

    def test_exact_adapter_types_and_bounded_configuration_have_no_effects(
        self,
    ) -> None:
        original = fixture(self.store)
        for kind, value in (
            ("calls", True),
            ("tokens", -1),
            ("cost_microunits", 2**64),
            ("time_ms", 1.0),
            ("time_ms", float("inf")),
            ("per_call", True),
            ("script", []),
            ("cursor", ()),
            ("clock", True),
            ("clock", float("nan")),
            ("provider", object()),
        ):
            with self.subTest(kind=kind, value=value):
                snapshot, experiment, original_provider = original
                experiment = copy.deepcopy(experiment)
                provider = ev.ScriptedProvider(
                    original_provider.script, clock=ev.VirtualClock()
                )
                if kind in {"calls", "tokens", "time_ms", "cost_microunits"}:
                    object.__setattr__(experiment.configuration.limits, kind, value)
                elif kind == "per_call":
                    object.__setattr__(
                        experiment.configuration.per_call, "tokens", value
                    )
                elif kind == "script":
                    provider.script = value
                elif kind == "cursor":
                    provider.calls = value
                elif kind == "clock":
                    provider.clock.now = value
                elif kind == "provider":
                    provider = value
                try:
                    object.__setattr__(
                        experiment, "config_sha256", experiment.configuration.sha256
                    )
                except ValueError:
                    pass  # Nonfinite JSON is itself invalid admission input.
                before = contents(self.store)
                with patch.object(ev.ScriptedProvider, "complete") as dispatch:
                    with self.assertRaises(ValueError):
                        invoke(self.store, (snapshot, experiment, provider))
                    dispatch.assert_not_called()
                self.assertEqual(contents(self.store), before)

    def test_warmed_provider_rejected_before_writes(self) -> None:
        ready = prefixed(self.store)
        provider = ready[2]
        consume(provider)
        before = contents(self.store)
        with self.assertRaisesRegex(ValueError, "pristine provider cursor"):
            invoke(self.store, ready)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(contents(self.store), before)
        cold = ev.ScriptedProvider(provider.script, clock=ev.VirtualClock())
        result = invoke(self.store, ready, provider=cold)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.candidate_score, 0.0)

    def test_cached_consumed_provider_recovers_without_writes_or_calls(self) -> None:
        ready = fixture(self.store)
        report = invoke(self.store, ready)
        before = contents(self.store)
        with patch.object(
            ev.ScriptedProvider,
            "complete",
            side_effect=AssertionError("cached dispatch"),
        ) as dispatch:
            replay = invoke(self.store, ready)
            dispatch.assert_not_called()
        self.assertEqual(storage.pack(replay), storage.pack(report))
        self.assertEqual(contents(self.store), before)
        with self.assertRaisesRegex(ValueError, "immutable experiment conflict"):
            invoke(self.store, ready, budget_id="unrelated")
        self.assertEqual(contents(self.store), before)

    def test_passing_replay_rejects_rehashed_score_and_missing_settlement(self) -> None:
        ready = fixture(self.store)
        report = invoke(self.store, ready)
        original = hashlib.sha256(storage.pack(report)).hexdigest()
        changed = replace(report, baseline_score=0.0)
        changed_sha = hashlib.sha256(storage.pack(changed)).hexdigest()
        with self.store.transaction() as tx:
            self.store.put(tx, "report", changed_sha, changed)
            tx.execute("UPDATE experiments SET report=?", (changed_sha,))

        def assert_denied() -> None:
            before = contents(self.store)
            with (
                patch.object(
                    ev.ScriptedProvider,
                    "complete",
                    side_effect=AssertionError("dispatch"),
                ) as dispatch,
                storage.Store(self.path) as reopened,
            ):
                with self.assertRaisesRegex(ValueError, "passing evaluation evidence"):
                    invoke(reopened, ready)
                dispatch.assert_not_called()
            self.assertEqual(contents(self.store), before)

        assert_denied()
        with self.store.transaction() as tx:
            tx.execute("UPDATE experiments SET report=?", (original,))
            tx.execute(
                "UPDATE attempts SET outcome='unknown', receipt=NULL "
                "WHERE ticket=(SELECT max(ticket) FROM attempts)"
            )
        assert_denied()

    def test_rejected_shared_budget_and_failed_claim_roll_back_all_rows(self) -> None:
        ready = fixture(self.store)
        de.SQLiteBudget(
            self.store, "budget", ev.Limits(1, 1, 1, 1), clock_ms=ready[2].clock
        )
        before = contents(self.store)
        with self.assertRaisesRegex(ValueError, "budget configuration conflict"):
            invoke(self.store, ready)
        self.assertEqual(contents(self.store), before)
        with patch.object(self.store, "put", side_effect=ValueError("claim rejected")):
            with self.assertRaisesRegex(ValueError, "claim rejected"):
                invoke(self.store, ready, budget_id="new-budget")
        self.assertEqual(contents(self.store), before)
        self.assertEqual(ready[2].calls, [])

    def test_claim_authority_callback_drift_has_no_durable_effects(self) -> None:
        ready = prefixed(self.store)
        for drift in ("cursor", "script"):
            with self.subTest(drift=drift):
                provider = ev.ScriptedProvider(ready[2].script, clock=ev.VirtualClock())
                checks = 0

                def check() -> None:
                    nonlocal checks
                    checks += 1
                    if checks == 2:
                        if drift == "cursor":
                            consume(provider)
                        else:
                            provider.script = ()

                before = contents(self.store)
                with patch.object(de, "use_guard", return_value=check):
                    with self.assertRaises(ValueError):
                        invoke(self.store, ready, provider=provider)
                self.assertEqual(contents(self.store), before)
                self.assertEqual(len(provider.calls), 1 if drift == "cursor" else 0)

    def test_checkpoint_cursor_drift_never_dispatches_or_refunds(self) -> None:
        for phase in ("after_experiment_commit", "after_budget_reservation"):
            with (
                self.subTest(phase=phase),
                storage.Store(str(self.path) + phase) as store,
            ):
                ready = prefixed(store)
                provider = ready[2]
                fired = False

                def checkpoint(point: str) -> None:
                    nonlocal fired
                    if point == phase and not fired:
                        fired = True
                        consume(provider)

                report = invoke(store, ready, checkpoint=checkpoint)
                self.assertEqual(report.status, "blocked")
                self.assertEqual(report.reason, "provider_cursor_changed")
                self.assertEqual(
                    len(provider.calls), 1, "only the injected callback call may exist"
                )
                self.assertEqual(report.attempts, ())
                reserved = int(phase == "after_budget_reservation")
                self.assertEqual(dict(report.usage)["calls"], reserved)
                self.assertEqual(dict(report.usage)["unresolved_calls"], reserved)
                self.assertEqual(
                    store.db.execute("SELECT count(*) FROM attempts").fetchone()[0],
                    reserved,
                )

    def test_dispatch_authority_callback_drift_has_no_runner_calls(self) -> None:
        for check_number in (3, 4):
            with (
                self.subTest(check_number=check_number),
                storage.Store(str(self.path) + str(check_number)) as store,
            ):
                ready = prefixed(store)
                count = 0

                def check() -> None:
                    nonlocal count
                    count += 1
                    if count == check_number:
                        consume(ready[2])

                with patch.object(de, "use_guard", return_value=check):
                    report = invoke(store, ready)
                self.assertEqual(report.reason, "provider_cursor_changed")
                self.assertEqual(len(ready[2].calls), 1)
                self.assertEqual(report.attempts, ())

    def test_script_drift_after_reservation_halts_with_reservation(self) -> None:
        ready = fixture(self.store)

        def checkpoint(point: str) -> None:
            if point == "after_budget_reservation":
                ready[2].script = ()

        report = invoke(self.store, ready, checkpoint=checkpoint)
        self.assertEqual(report.reason, "adapter_configuration_changed")
        self.assertEqual(ready[2].calls, [])
        self.assertEqual(dict(report.usage)["unresolved_calls"], 1)

    def test_final_completion_cursor_drift_stops_every_result_path(self) -> None:
        for outcome in (
            "passed",
            "threshold_failed",
            "unsafe",
            "unknown_safety",
            "timeout",
        ):
            with (
                self.subTest(outcome=outcome),
                storage.Store(str(self.path) + outcome) as store,
            ):
                snapshot, experiment, provider = fixture(store)
                count = len(provider.script)
                if outcome == "threshold_failed":
                    provider.script = tuple(
                        replace(reply, content=b"wrong")
                        if index >= count // 2
                        else reply
                        for index, reply in enumerate(provider.script)
                    )
                elif outcome in {"unsafe", "unknown_safety"}:
                    provider.script = provider.script[:-1] + (
                        replace(
                            provider.script[-1],
                            safety=False if outcome == "unsafe" else None,
                        ),
                    )
                elif outcome == "timeout":
                    provider.script = provider.script[:-1] + (
                        replace(provider.script[-1], outcome="timeout", content=None),
                    )
                provider.script += (ev.Completion(b"extra", 1, 1, True),)
                config = replace(
                    experiment.configuration, provider_script_sha256=provider.sha256
                )
                experiment = replace(
                    experiment, configuration=config, config_sha256=config.sha256
                )
                original = provider.complete

                def complete(request: Any) -> Any:
                    reply = original(request)
                    if len(provider.calls) == count:
                        original(request)
                    return reply

                with patch.object(provider, "complete", side_effect=complete):
                    report = invoke(store, (snapshot, experiment, provider))
                self.assertEqual(report.status, "blocked")
                self.assertEqual(report.reason, "provider_cursor_changed")
                self.assertEqual(
                    report.safety,
                    False
                    if outcome == "unsafe"
                    else None
                    if outcome == "unknown_safety"
                    else True,
                )
                self.assertEqual(len(provider.calls), count + 1)
                self.assertEqual(dict(report.usage)["calls"], count)
                self.assertEqual(dict(report.usage)["unresolved_calls"], 0)
                self.assertEqual(
                    dict(report.usage)["reason"], "provider_cursor_changed"
                )
                ledger = de.SQLiteBudget(
                    store, "budget", config.limits, clock_ms=provider.clock
                )
                with self.assertRaisesRegex(ev.Halt, "provider_cursor_changed"):
                    ledger.reserve(ev.Reservation(1, 1))

    def test_concurrent_claim_rejects_loser_without_ledger_or_dispatch(self) -> None:
        ready = fixture(self.store)
        claimed, release = threading.Event(), threading.Event()

        def owner() -> Any:
            with storage.Store(self.path) as store:
                provider = ev.ScriptedProvider(ready[2].script, clock=ev.VirtualClock())

                def checkpoint(point: str) -> None:
                    if point == "after_experiment_commit":
                        claimed.set()
                        if not release.wait(10):
                            raise TimeoutError("claim release")

                return invoke(store, ready, provider=provider, checkpoint=checkpoint)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(owner)
            self.assertTrue(claimed.wait(10))
            try:
                before = contents(self.store)
                with self.assertRaisesRegex(ev.Halt, "interrupted_or_owned_experiment"):
                    invoke(self.store, ready)
                with self.assertRaisesRegex(
                    ValueError, "immutable experiment conflict"
                ):
                    invoke(self.store, ready, budget_id="unrelated")
                self.assertEqual(contents(self.store), before)
                self.assertEqual(ready[2].calls, [])
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10).status, "passed")

    def test_admission_removal_mutation_exposes_poisoned_claim(self) -> None:
        source = (ROOT / "refinery/durable_evaluation.py").read_text()
        anchor = "validate_provider(experiment, provider)"
        self.assertEqual(source.count(anchor), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutant.py"
            path.write_text(source.replace(anchor, "None  # removed admission"))
            spec = importlib.util.spec_from_file_location(
                "refinery.mutant_durable_admission", path
            )
            if spec is None or spec.loader is None:
                self.fail("mutation loader unavailable")
            mutant = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mutant)
            snapshot, experiment, provider = fixture(self.store)
            provider.script = ()
            before = contents(self.store)
            with self.assertRaisesRegex(ValueError, "adapter configuration changed"):
                mutant.run(
                    self.store,
                    storage.VERSION,
                    "evaluation",
                    snapshot,
                    experiment,
                    provider=provider,
                    budget_id="budget",
                    synthetic_fixture=True,
                )
            self.assertNotEqual(contents(self.store), before)
            self.assertEqual(
                self.store.db.execute("SELECT state FROM experiments").fetchall(),
                [("running",)],
            )
            self.assertEqual(provider.calls, [])

    def test_fresh_process_recovery_has_no_calls_or_changed_rows(self) -> None:
        ready = fixture(self.store)
        result = invoke(self.store, ready)
        before = contents(self.store)
        program = """
import json, socket, sys
from unittest.mock import patch
from test_evaluation_admission import fixture, invoke, storage
with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
    with storage.Store(sys.argv[1]) as store:
        ready = fixture(store)
        with patch.object(ready[2], 'complete', side_effect=AssertionError('cached dispatch')) as dispatch:
            result = invoke(store, ready)
            dispatch.assert_not_called()
        print(json.dumps({'result': storage.pack(result).hex(), 'calls': len(ready[2].calls)}))
"""
        child = subprocess.run(
            [sys.executable, "-c", program, str(self.path)],
            cwd=ROOT,
            env=dict(
                os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "tests")
            ),
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(
            json.loads(child.stdout), {"result": storage.pack(result).hex(), "calls": 0}
        )
        self.assertEqual(contents(self.store), before)

    def test_dispatch_cursor_guard_mutation_exposes_changed_result(self) -> None:
        source = (ROOT / "refinery/evaluation.py").read_text()
        anchor = "if len(provider.calls) != cursor + len(attempts):"
        self.assertEqual(source.count(anchor), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutant.py"
            path.write_text(source.replace(anchor, "if False:"))
            spec = importlib.util.spec_from_file_location(
                "refinery.mutant_admission", path
            )
            if spec is None or spec.loader is None:
                self.fail("mutation loader unavailable")
            mutant = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mutant
            self.addCleanup(sys.modules.pop, spec.name)
            spec.loader.exec_module(mutant)
            # Keep actual adapter/dataclass identity; mutate only the guard.
            for attribute, value in vars(ev).items():
                if isinstance(value, type):
                    setattr(mutant, attribute, value)
            ready = prefixed(self.store)

            def checkpoint(point: str) -> None:
                if point == "after_experiment_commit":
                    consume(ready[2])

            with patch.object(de, "evaluate", mutant.evaluate):
                report = invoke(self.store, ready, checkpoint=checkpoint)
            self.assertEqual(report.status, "passed")
            self.assertEqual(report.candidate_score, 1.0)
            self.assertEqual(len(ready[2].calls), len(report.attempts) + 1)
            self.assertEqual(dict(report.usage)["calls"], len(report.attempts))


if __name__ == "__main__":
    unittest.main()
