"""Real transport/ledger/serialization tests, never a stand-in for DSPy execution."""

from __future__ import annotations

import importlib
import socket
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery.dspy_optimizer import (
    Dispatch,
    Settings,
    binding,
    case,
    decode_candidate,
    encode_candidate,
    optimize,
)
from refinery.durable_evaluation import SQLiteBudget
from refinery.lineage import digest

storage: Any = importlib.import_module("refinery.storage")
fixture: Any = importlib.import_module("test_durable").setup
legacy: Any = ev


class DSPyBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = storage.Store(Path(temporary.name) / "store.sqlite")
        self.addCleanup(self.store.close)
        snapshot, _, _ = fixture(self.store)
        self.dataset, self.split = self.store.lineage(snapshot)
        self.train = tuple(
            e
            for e in self.dataset.examples
            if self.split.partition(e.source.family_id) == "train"
        )
        self.heldout = tuple(
            e
            for e in self.dataset.examples
            if self.split.partition(e.source.family_id) == "heldout"
        )
        self.clock = legacy.VirtualClock()
        self.provider = legacy.ScriptedProvider(
            tuple(ev.Completion(e.gold, 1, 1, True, 1) for e in self.train),
            clock=self.clock,
        )
        self.limits = ev.Limits(100, 1000, 1000, 1000)
        self.budget: Any = SQLiteBudget(
            self.store,
            "shared",
            self.limits,
            clock_ms=self.clock,
            experiment="optimizer",
        )
        self.reservation = ev.Reservation(10, 10)
        self.check_use = lambda: None
        denied = patch.object(
            socket, "socket", side_effect=AssertionError("unexpected network")
        )
        self.network = denied.start()
        self.addCleanup(denied.stop)
        self.addCleanup(self.network.assert_not_called)

    def dispatch(self, **changes: Any) -> Dispatch:
        args = dict(
            stage="optimization",
            artifact_sha256="a" * 64,
            provider=self.provider,
            budget=self.budget,
            per_call=self.reservation,
            check_use=self.check_use,
        )
        args.update(changes)
        return Dispatch(self.train, self.split, **args)

    def call(self, dispatch: Dispatch, example: Any = None) -> Any:
        return dispatch.invoke(
            lambda **_: dispatch.forward([{"role": "user", "content": "synthetic"}]),
            case(example or self.train[0]),
        )

    def test_actual_sqlite_receipt_is_attributed_before_return(self) -> None:
        dispatch = self.dispatch()
        self.call(dispatch)
        self.assertEqual(self.budget.snapshot()["calls"], 1)
        request, receipt = self.store.db.execute(
            "SELECT request,receipt FROM attempts"
        ).fetchone()
        self.assertEqual(storage.unpack(request), self.provider.calls[0])
        self.assertEqual(storage.unpack(receipt), dispatch.attempts[0])
        self.assertEqual(dispatch.attempts[0].request.stage, "optimization")
        self.assertEqual(len(dispatch.messages), 1)

    def test_unknown_usage_keeps_reservation_and_blocks_retry(self) -> None:
        for field in ("tokens", "cost_microunits"):
            with self.subTest(field=field):
                changes: dict[str, Any] = {field: None}
                provider = legacy.ScriptedProvider(
                    (replace(ev.Completion(b"ok", 1, 1, True, 1), **changes),),
                    clock=self.clock,
                )
                budget: Any = SQLiteBudget(
                    self.store,
                    field,
                    self.limits,
                    clock_ms=self.clock,
                    experiment=field,
                )
                dispatch = self.dispatch(provider=provider, budget=budget)
                with self.assertRaisesRegex(ev.Halt, "unknown_or_invalid_usage"):
                    self.call(dispatch)
                with self.assertRaises(ev.Halt):
                    self.call(dispatch)
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(budget.snapshot()["unresolved_calls"], 1)

    def test_cancel_after_reservation_has_no_provider_effect(self) -> None:
        budget: Any = SQLiteBudget(
            self.store,
            "cancel",
            self.limits,
            clock_ms=self.clock,
            experiment="cancel",
            checkpoint=lambda _: self.budget.cancel(),
        )
        dispatch = self.dispatch(budget=budget, check_use=lambda: self.budget.check())
        with self.assertRaisesRegex(ev.Halt, "cancelled"):
            self.call(dispatch)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(budget.snapshot()["unresolved_calls"], 1)

    def test_shared_budget_exhaustion_prevents_second_dispatch(self) -> None:
        budget: Any = SQLiteBudget(
            self.store, "limited", ev.Limits(1, 1000, 1000, 1000), clock_ms=self.clock
        )
        self.call(self.dispatch(budget=budget))
        with self.assertRaisesRegex(ev.Halt, "calls_exhausted"):
            self.call(self.dispatch(budget=budget))
        self.assertEqual(len(self.provider.calls), 1)

    def test_provider_failure_cannot_replay(self) -> None:
        provider = legacy.ScriptedProvider((), clock=self.clock)
        dispatch = self.dispatch(provider=provider)
        original = ev.ScriptedProvider.complete
        observed: list[Any] = []

        def observe(instance: Any, request: Any) -> Any:
            observed.append(request)
            return original(instance, request)

        with patch.object(ev.ScriptedProvider, "complete", observe):
            with self.assertRaisesRegex(ev.Halt, "^provider_unknown$"):
                self.call(dispatch)
            ledger = self.store.db.execute("SELECT payload FROM budgets").fetchall()
            attempts = self.store.db.execute("SELECT * FROM attempts").fetchall()
            with self.assertRaisesRegex(ev.Halt, "^provider_unknown$"):
                self.call(dispatch)
        self.assertEqual(len(observed), 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(dispatch.expected_calls, ())
        self.assertEqual(self.budget.snapshot()["unresolved_calls"], 1)
        self.assertEqual(
            ledger, self.store.db.execute("SELECT payload FROM budgets").fetchall()
        )
        self.assertEqual(
            attempts, self.store.db.execute("SELECT * FROM attempts").fetchall()
        )

    def test_binding_rejection_retains_first_stop_without_clock_callback(self) -> None:
        for durable in (False, True):
            for prior in (
                None,
                "provider_unknown",
                "cancelled",
                "unknown_or_invalid_usage",
            ):
                with self.subTest(durable=durable, prior=prior):
                    budget: Any = (
                        SQLiteBudget(
                            self.store,
                            "clock-" + str(durable) + str(prior),
                            self.limits,
                            clock_ms=self.clock,
                        )
                        if durable
                        else ev.Budget(self.limits, clock_ms=self.clock)
                    )
                    dispatch = self.dispatch(budget=budget)
                    if prior is not None:
                        budget.stop(prior)
                    with patch.object(
                        budget,
                        "clock_ms",
                        side_effect=AssertionError("untrusted clock invoked"),
                    ) as clock:
                        with self.assertRaisesRegex(
                            ev.Halt, "^" + (prior or "dispatch_binding_changed") + "$"
                        ):
                            dispatch.check_binding()
                        clock.assert_not_called()
                    self.assertEqual(dispatch.expected_calls, ())
                    self.assertEqual(self.provider.calls, [])
                    self.assertEqual(budget.snapshot()["calls"], 0)

    def test_foreign_cursor_retains_terminal_reason_but_never_adopts_cursor(
        self,
    ) -> None:
        for prior in ("cancelled", "provider_unknown", "unknown_or_invalid_usage"):
            with self.subTest(prior=prior):
                provider = legacy.ScriptedProvider((), clock=self.clock)
                budget = ev.Budget(self.limits, clock_ms=self.clock)
                dispatch = self.dispatch(provider=provider, budget=budget)
                budget.stop(prior)
                provider.calls.append(
                    ev.Request(
                        "optimization",
                        "rogue",
                        "1",
                        "a" * 64,
                        "b" * 64,
                        self.split.sha256,
                        1,
                    )
                )
                with self.assertRaisesRegex(ev.Halt, "^" + prior + "$"):
                    self.call(dispatch)
                self.assertEqual(dispatch.expected_calls, ())
                self.assertEqual(dispatch.attempts, [])
                self.assertEqual(budget.snapshot()["calls"], 0)

    def test_malformed_terminal_reason_falls_back_to_binding_failure(self) -> None:
        for reason in (None, 42, "", "private text\n/path", "x" * 129):
            with self.subTest(reason=reason):
                dispatch = self.dispatch()
                dispatch.expected_calls = (
                    ev.Request(
                        "optimization",
                        "rogue",
                        "1",
                        "a" * 64,
                        "b" * 64,
                        self.split.sha256,
                        1,
                    ),
                )
                with patch.object(
                    self.budget, "snapshot", return_value={"reason": reason}
                ):
                    with self.assertRaisesRegex(ev.Halt, "^dispatch_binding_changed$"):
                        dispatch.check_binding()
                self.assertEqual(self.provider.calls, [])

    def test_holdout_cannot_enter_training_or_unattributed_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbidden partition"):
            Dispatch(
                self.heldout,
                self.split,
                stage="optimization",
                artifact_sha256="a" * 64,
                provider=self.provider,
                budget=self.budget,
                per_call=self.reservation,
                check_use=self.check_use,
            )
        with self.assertRaisesRegex(ev.Halt, "forbidden_partition"):
            self.call(self.dispatch(), self.heldout[0])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_deliberately_broken_allowlist_is_observed_independently(self) -> None:
        dispatch = self.dispatch()
        # Deliberately violate the guard, then observe actual provider requests,
        # rather than trusting a result label emitted by the mutated component.
        heldout = self.heldout[0]
        dispatch.allowed[case(heldout)] = heldout
        self.call(dispatch, heldout)
        allowed = {e.output.output_id for e in self.train}
        with self.assertRaises(AssertionError):
            self.assertTrue(all(r.output_id in allowed for r in self.provider.calls))

    def test_callback_effect_is_detected_without_retroactive_credit(self) -> None:
        fired = False

        def rogue() -> None:
            nonlocal fired
            if not fired:
                fired = True
                self.provider.complete(
                    ev.Request(
                        "optimization",
                        "rogue",
                        "1",
                        "a" * 64,
                        "b" * 64,
                        self.split.sha256,
                        1,
                    )
                )

        dispatch = self.dispatch(check_use=rogue)
        with self.assertRaisesRegex(ev.Halt, "dispatch_binding_changed"):
            self.call(dispatch)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.budget.snapshot()["calls"], 0)
        self.assertEqual(dispatch.attempts, [])
        self.assertEqual(self.budget.snapshot()["reason"], "dispatch_binding_changed")

    def test_nested_callback_dispatch_is_rejected(self) -> None:
        dispatch = self.dispatch()
        dispatch.check_use = lambda: dispatch.forward([])
        with self.assertRaisesRegex(ev.Halt, "reentrant_dispatch"):
            self.call(dispatch)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_warmed_optimizer_and_configuration_mutation_are_rejected(self) -> None:
        self.call(self.dispatch())
        with self.assertRaisesRegex(ValueError, "pristine provider"):
            optimize(
                self.dataset,
                self.split,
                provider=self.provider,
                budget=self.budget,
                per_call=self.reservation,
                check_use=self.check_use,
            )
        dispatch = self.dispatch()
        dispatch.check_use = lambda: setattr(
            self.budget, "limits", ev.Limits(999, 9999, 9999, 9999)
        )
        with self.assertRaisesRegex(ev.Halt, "dispatch_binding_changed"):
            self.call(dispatch)
        self.assertEqual(len(self.provider.calls), 1)

    def test_candidate_digest_lineage_and_demo_tampering(self) -> None:
        _, config = binding(
            self.dataset,
            self.split,
            Settings(),
            self.provider,
            self.budget,
            self.reservation,
        )
        demos = ((case(self.train[0]), self.train[0].gold.decode()),)
        artifact = encode_candidate(demos, self.train, self.split, config)
        sha = digest(artifact)
        self.assertEqual(
            decode_candidate(artifact, sha, self.dataset, self.split, config), demos
        )
        for altered, expected, setting in (
            (artifact + b" ", sha, config),
            (artifact, "0" * 64, config),
            (artifact, sha, "0" * 64),
        ):
            with self.assertRaises(ValueError):
                decode_candidate(altered, expected, self.dataset, self.split, setting)
        with self.assertRaises(ValueError):
            encode_candidate(
                ((case(self.heldout[0]), self.heldout[0].gold.decode()),),
                self.train,
                self.split,
                config,
            )
        self.assertEqual(self.provider.calls, [])

    def test_missing_framework_fails_closed_before_dispatch(self) -> None:
        with patch(
            "refinery.dspy_optimizer.framework", side_effect=ModuleNotFoundError("dspy")
        ):
            with self.assertRaises(ModuleNotFoundError):
                optimize(
                    self.dataset,
                    self.split,
                    provider=self.provider,
                    budget=self.budget,
                    per_call=self.reservation,
                    check_use=self.check_use,
                )
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_unknown_provider_is_rejected_before_reading_properties(self) -> None:
        class Foreign(legacy.ScriptedProvider):  # type: ignore[misc]
            @property
            def sha256(self) -> str:
                self.fail_if_read = True
                raise AssertionError("foreign provider property executed")

        provider = Foreign((), clock=self.clock)
        with self.assertRaisesRegex(ValueError, "offline adapters"):
            binding(
                self.dataset,
                self.split,
                Settings(),
                provider,
                self.budget,
                self.reservation,
            )
        self.assertFalse(getattr(provider, "fail_if_read", False))
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_invalid_bounds_and_mutated_split_have_no_calls(self) -> None:
        for values in (
            {"max_rounds": 0},
            {"max_rounds": 4},
            {"max_bootstrapped_demos": True},
        ):
            with self.assertRaises(ValueError):
                Settings(**values)
        with self.assertRaises(ValueError):
            binding(
                self.dataset,
                replace(self.split, dataset_sha256="0" * 64),
                Settings(),
                self.provider,
                self.budget,
                self.reservation,
            )
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
