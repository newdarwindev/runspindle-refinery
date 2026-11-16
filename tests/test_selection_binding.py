"""Selection guards exercise actual scripted dispatch, ledgers and SQLite."""

from __future__ import annotations

import importlib
import inspect
import socket
import textwrap
import unittest
from dataclasses import replace
from typing import Any, Callable
from unittest.mock import patch

ev: Any = importlib.import_module("refinery.evaluation")
opt: Any = importlib.import_module("refinery.optimization")
durable: Any = importlib.import_module("refinery.durable_optimization")
storage: Any = importlib.import_module("refinery.storage")
serving: Any = importlib.import_module("refinery.serving")
dataset_fixture: Any = importlib.import_module("test_evaluation").dataset_fixture
lineage_fixture: Any = importlib.import_module("test_durable").setup

LIMITS = ev.Limits(200, 2000, 2000, 2000)


class SelectionBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(socket, "socket", side_effect=AssertionError("network"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(self.network.assert_not_called)
        loader = patch.object(
            serving.LocalServing, "load", side_effect=AssertionError("load")
        )
        self.loader = loader.start()
        self.addCleanup(loader.stop)
        self.addCleanup(self.loader.assert_not_called)
        self.observed: list[Any] = []
        self.after_complete: Callable[[Any, Any], None] = lambda *_: None
        original = ev.ScriptedProvider.complete

        def observe(provider: Any, request: Any) -> Any:
            self.observed.append(request)
            reply = original(provider, request)
            self.after_complete(provider, request)
            return reply

        calls = patch.object(
            ev.ScriptedProvider, "complete", autospec=True, side_effect=observe
        )
        calls.start()
        self.addCleanup(calls.stop)

    def local(self, *, prefix: bool = False) -> tuple[Any, ...]:
        data, split = dataset_fixture()
        train = tuple(
            e for e in data.examples if split.partition(e.source.family_id) == "train"
        )
        clock = ev.VirtualClock()
        script = tuple(ev.Completion(e.gold, 1, 1, True, 1) for e in train)
        if prefix:
            script = (ev.Completion(b"prefix", 1, 1, True, 1),) + script
        provider = ev.ScriptedProvider(script, clock=clock)
        ledger = ev.Budget(LIMITS, clock_ms=clock)
        return data, split, train, provider, ledger, ev.Reservation(10, 10)

    def search(self, values: tuple[Any, ...], **kwargs: Any) -> Any:
        data, split, _, provider, ledger, reservation = values
        return opt.optimize(
            data,
            split,
            (b"fixed",),
            provider=provider,
            budget=ledger,
            per_call=reservation,
            **kwargs,
        )

    def consume(self, provider: Any) -> None:
        provider.complete(
            ev.Request("external", "prefix", "1", "0" * 64, "0" * 64, "0" * 64, 1)
        )

    def mutate(self, kind: str, provider: Any, ledger: Any, reservation: Any) -> None:
        if kind == "script":
            provider.script = tuple(
                ev.Completion(b"changed", 1, 1, True, 1) for _ in provider.script
            )
        elif kind == "clock":
            provider.clock = ev.VirtualClock()
            ledger.clock_ms = provider.clock
        elif kind == "limits":
            ledger.limits = replace(ledger.limits, calls=201)
        elif kind == "reservation":
            object.__setattr__(reservation, "tokens", 11)
        elif kind == "cursor":
            self.consume(provider)
        else:
            raise AssertionError("unknown mutation")

    def test_use_callback_drift_blocks_before_reservation(self) -> None:
        for kind in ("script", "clock", "limits", "reservation", "cursor"):
            with self.subTest(kind=kind):
                values = self.local()
                _, _, _, provider, ledger, reservation = values
                before = len(self.observed)
                result = self.search(
                    values,
                    check_use=lambda: self.mutate(kind, provider, ledger, reservation),
                )
                self.assertEqual(result.status, "blocked")
                self.assertIsNone(result.selected)
                self.assertEqual(ledger.snapshot()["calls"], 0)
                self.assertEqual(ledger.snapshot()["unresolved_calls"], 0)
                self.assertEqual(len(self.observed) - before, int(kind == "cursor"))
                self.assertEqual(
                    result.reason,
                    "provider_cursor_changed"
                    if kind == "cursor"
                    else "adapter_configuration_changed",
                )
                with self.assertRaises(ev.Halt):
                    ledger.reserve(ev.Reservation(1, 1))

    def test_reserve_callback_drift_keeps_unknown_charge_and_zero_runner_calls(
        self,
    ) -> None:
        for kind in ("script", "clock", "cursor"):
            with self.subTest(kind=kind):
                values = self.local()
                _, _, _, provider, ledger, reservation = values
                original = ledger.reserve
                before = len(self.observed)

                def reserve(*args: Any, **kwargs: Any) -> Any:
                    ticket = original(*args, **kwargs)
                    self.mutate(kind, provider, ledger, reservation)
                    return ticket

                with patch.object(ledger, "reserve", side_effect=reserve):
                    result = self.search(values)
                self.assertEqual(result.status, "blocked")
                self.assertIsNone(result.selected)
                self.assertEqual(ledger.snapshot()["calls"], 1)
                self.assertEqual(ledger.snapshot()["unresolved_calls"], 1)
                self.assertIsNone(ledger.snapshot()["tokens"])
                self.assertEqual(len(self.observed) - before, int(kind == "cursor"))
                self.assertTrue(
                    all(r.stage == "external" for r in self.observed[before:])
                )

    def test_second_use_check_blocks_dispatch_after_reservation(self) -> None:
        values = self.local()
        _, _, _, provider, ledger, reservation = values
        checks = 0

        def authority() -> None:
            nonlocal checks
            checks += 1
            if checks == 2:
                self.mutate("script", provider, ledger, reservation)

        result = self.search(values, check_use=authority)
        self.assertEqual(result.reason, "adapter_configuration_changed")
        self.assertEqual(self.observed, [])
        self.assertEqual(ledger.snapshot()["calls"], 1)
        self.assertEqual(ledger.snapshot()["unresolved_calls"], 1)

    def test_settle_and_final_snapshot_callbacks_cannot_certify_selection(self) -> None:
        for phase in ("settle", "checked_snapshot"):
            with self.subTest(phase=phase):
                values = self.local()
                _, _, train, provider, ledger, reservation = values
                original = getattr(ledger, phase)
                before = len(self.observed)

                def callback(*args: Any, **kwargs: Any) -> Any:
                    value = original(*args, **kwargs)
                    self.mutate("script", provider, ledger, reservation)
                    return value

                with patch.object(ledger, phase, side_effect=callback):
                    result = self.search(values)
                count = 1 if phase == "settle" else len(train)
                self.assertEqual(len(self.observed) - before, count)
                self.assertEqual(ledger.snapshot()["calls"], count)
                self.assertEqual(ledger.snapshot()["tokens"], count)
                self.assertEqual(ledger.snapshot()["unresolved_calls"], 0)
                self.assertEqual(result.status, "blocked")
                self.assertIsNone(result.selected)
                self.assertEqual(
                    dict(result.usage)["reason"], "adapter_configuration_changed"
                )

    def test_last_completion_cursor_drift_is_not_a_passing_budgeted_result(
        self,
    ) -> None:
        values = self.local()
        _, _, train, provider, ledger, _ = values
        # Supply one extra receipt for the callback's independent call.
        provider.script += (ev.Completion(b"external", 1, 1, True, 1),)

        def after(instance: Any, request: Any) -> None:
            if request.stage == "optimization" and len(instance.calls) == len(train):
                self.consume(instance)

        self.after_complete = after
        result = self.search(values)
        self.assertEqual(result.reason, "provider_cursor_changed")
        self.assertIsNone(result.selected)
        self.assertEqual(len(self.observed), len(train) + 1)
        self.assertEqual(ledger.snapshot()["calls"], len(train))
        self.assertEqual(ledger.snapshot()["unresolved_calls"], 0)

    def test_stable_consumed_prefix_and_retry_keep_shared_budget(self) -> None:
        values = self.local(prefix=True)
        _, _, train, provider, ledger, _ = values
        ticket = ledger.reserve(ev.Reservation(10, 10))
        self.consume(provider)
        ledger.settle(ticket, tokens=1, cost_microunits=1)
        result = self.search(values)
        self.assertEqual(result.status, "selected")
        self.assertEqual(len(self.observed), len(train) + 1)
        self.assertEqual(dict(result.usage)["calls"], len(train) + 1)
        values = self.local()
        _, _, train, provider, ledger, _ = values
        provider.script = (
            ev.Completion(None, 1, 1, True, 1, "timeout"),
        ) + provider.script
        result = self.search(values, max_attempts=2)
        self.assertEqual(result.status, "selected")
        self.assertEqual(len(result.attempts), len(train) + 1)
        self.assertEqual(ledger.snapshot()["calls"], len(train) + 1)

    def durable_fixture(self, store: Any) -> tuple[Any, ...]:
        snapshot, _, _ = lineage_fixture(store)
        data, split = store.lineage(snapshot)
        train = tuple(
            e for e in data.examples if split.partition(e.source.family_id) == "train"
        )
        provider = ev.ScriptedProvider(
            tuple(ev.Completion(e.gold, 1, 1, True, 1) for e in train),
            clock=ev.VirtualClock(),
        )
        return snapshot, train, provider

    def run_durable(
        self, store: Any, snapshot: str, provider: Any, **kwargs: Any
    ) -> Any:
        return durable.run(
            store,
            storage.VERSION,
            "search",
            snapshot,
            (b"fixed",),
            provider=provider,
            limits=LIMITS,
            per_call=ev.Reservation(10, 10),
            budget_id="shared",
            synthetic_fixture=True,
            **kwargs,
        )

    def test_durable_preclaim_authority_drift_has_no_claim_or_ledger_effect(
        self,
    ) -> None:
        with storage.Store(":memory:") as store:
            snapshot, _, provider = self.durable_fixture(store)
            before = list(store.db.iterdump())

            def authority() -> None:
                provider.script = tuple(
                    ev.Completion(b"changed", 1, 1, True, 1) for _ in provider.script
                )

            with patch.object(durable, "use_guard", return_value=authority):
                with self.assertRaisesRegex(ev.Halt, "adapter_configuration_changed"):
                    self.run_durable(store, snapshot, provider)
            self.assertEqual(list(store.db.iterdump()), before)
            self.assertEqual(self.observed, [])

    def test_durable_checkpoint_drift_preserves_ownership_and_charges(self) -> None:
        for phase in (
            "after_selection_claim",
            "after_budget_reservation",
            "before_selection_commit",
        ):
            for kind in ("script", "cursor", "clock"):
                with (
                    self.subTest(phase=phase, kind=kind),
                    storage.Store(":memory:") as store,
                ):
                    snapshot, train, provider = self.durable_fixture(store)
                    if kind == "cursor":
                        provider.script += (ev.Completion(b"external", 1, 1, True, 1),)
                    original_script = provider.script
                    before = len(self.observed)

                    def checkpoint(point: str) -> None:
                        if point == phase:
                            if kind == "cursor":
                                self.consume(provider)
                            elif kind == "clock":
                                provider.clock = ev.VirtualClock()
                            else:
                                provider.script = tuple(
                                    ev.Completion(b"changed", 1, 1, True, 1)
                                    for _ in provider.script
                                )

                    with self.assertRaises(ev.Halt):
                        self.run_durable(
                            store, snapshot, provider, checkpoint=checkpoint
                        )
                    self.assertEqual(
                        store.db.execute("SELECT state FROM experiments").fetchone(),
                        ("running",),
                    )
                    self.assertEqual(
                        store.db.execute(
                            "SELECT count(*) FROM objects WHERE kind='selection'"
                        ).fetchone()[0],
                        0,
                    )
                    if phase == "after_selection_claim":
                        self.assertEqual(
                            store.db.execute("SELECT count(*) FROM budgets").fetchone()[
                                0
                            ],
                            0,
                        )
                    else:
                        state = storage.unpack(
                            store.db.execute("SELECT payload FROM budgets").fetchone()[
                                0
                            ]
                        )
                        expected = (
                            1 if phase == "after_budget_reservation" else len(train)
                        )
                        self.assertEqual(state["calls"], expected)
                        self.assertEqual(
                            len(state["pending"]),
                            int(phase == "after_budget_reservation"),
                        )
                        self.assertIn(
                            state["reason"],
                            {
                                "provider_cursor_changed",
                                "adapter_configuration_changed",
                            },
                        )
                    calls = self.observed[before:]
                    expected_runner = (
                        len(train) if phase == "before_selection_commit" else 0
                    )
                    self.assertEqual(
                        sum(r.stage == "optimization" for r in calls), expected_runner
                    )
                    original = ev.ScriptedProvider(
                        original_script, clock=ev.VirtualClock()
                    )
                    observed = len(self.observed)
                    with self.assertRaisesRegex(
                        ev.Halt, "interrupted_or_owned_selection"
                    ):
                        self.run_durable(store, snapshot, original)
                    self.assertEqual(len(self.observed), observed)

    def test_after_commit_drift_rejects_return_but_preserves_bound_cached_result(
        self,
    ) -> None:
        with storage.Store(":memory:") as store:
            snapshot, train, provider = self.durable_fixture(store)
            script = provider.script

            def checkpoint(point: str) -> None:
                if point == "after_selection_commit":
                    provider.script = tuple(
                        ev.Completion(b"changed", 1, 1, True, 1) for _ in script
                    )

            with self.assertRaisesRegex(ev.Halt, "adapter_configuration_changed"):
                self.run_durable(store, snapshot, provider, checkpoint=checkpoint)
            binding, state, sha = store.db.execute(
                "SELECT binding,state,report FROM experiments"
            ).fetchone()
            stored = store.get("selection", sha)
            self.assertEqual(state, "complete")
            self.assertEqual(stored.configuration_sha256, storage.unpack(binding)[3])
            original = ev.ScriptedProvider(script, clock=ev.VirtualClock())
            before = list(store.db.iterdump())
            cached = self.run_durable(store, snapshot, original)
            self.assertEqual(storage.pack(cached), storage.pack(stored))
            self.assertEqual(list(store.db.iterdump()), before)
            self.assertEqual(len(self.observed), len(train))

    def test_cached_return_authority_drift_does_not_change_stored_result(self) -> None:
        for kind in ("script", "cursor"):
            with self.subTest(kind=kind), storage.Store(":memory:") as store:
                snapshot, _, provider = self.durable_fixture(store)
                self.run_durable(store, snapshot, provider)
                fresh = ev.ScriptedProvider(provider.script, clock=ev.VirtualClock())
                before = list(store.db.iterdump())
                checks = 0

                def authority() -> None:
                    nonlocal checks
                    checks += 1
                    if checks == 3:
                        if kind == "cursor":
                            self.consume(fresh)
                        else:
                            fresh.script = tuple(
                                ev.Completion(b"changed", 1, 1, True, 1)
                                for _ in fresh.script
                            )

                with patch.object(durable, "use_guard", return_value=authority):
                    with self.assertRaises(ev.Halt):
                        self.run_durable(store, snapshot, fresh)
                self.assertEqual(list(store.db.iterdump()), before)

    def test_stable_durable_replay_accepts_consumed_cursor(self) -> None:
        with storage.Store(":memory:") as store:
            snapshot, train, provider = self.durable_fixture(store)
            result = self.run_durable(store, snapshot, provider)
            self.assertEqual(len(provider.calls), len(train))
            before = list(store.db.iterdump())
            replay = self.run_durable(store, snapshot, provider)
            self.assertEqual(storage.pack(replay), storage.pack(result))
            self.assertEqual(list(store.db.iterdump()), before)
            self.assertEqual(len(self.observed), len(train))

    def test_guard_removal_mutation_exposes_wrong_script_attribution(self) -> None:
        source = textwrap.dedent(inspect.getsource(opt.optimize))
        needle = "    def check_provider() -> None:\n"
        self.assertEqual(source.count(needle), 1)
        namespace = dict(vars(opt))
        exec(
            compile(
                source.replace(needle, needle + "        return\n"),
                "<selection-guard-mutation>",
                "exec",
            ),
            namespace,
        )
        values = self.local()
        _, _, train, provider, ledger, reservation = values
        original = provider.sha256
        result = namespace["optimize"](
            values[0],
            values[1],
            (b"fixed",),
            provider=provider,
            budget=ledger,
            per_call=reservation,
            check_use=lambda: self.mutate("script", provider, ledger, reservation),
        )
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.provider_script_sha256, original)
        self.assertNotEqual(result.provider_script_sha256, provider.sha256)
        self.assertEqual(len(self.observed), len(train))
        with self.assertRaises(AssertionError):
            self.assertEqual(result.provider_script_sha256, provider.sha256)


if __name__ == "__main__":
    unittest.main()
