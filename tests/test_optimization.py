"""Observe actual provider requests and shared ledger, never reported pass alone."""

from __future__ import annotations

import importlib
import importlib.util
import socket
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery.lineage import digest
from refinery.optimization import optimize

legacy: Any = ev
fixture: Any = importlib.import_module("test_evaluation").dataset_fixture
ROOT = Path(__file__).resolve().parents[1]


class OptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data, self.split = fixture()
        self.train = tuple(
            e
            for e in self.data.examples
            if self.split.partition(e.source.family_id) == "train"
        )
        self.heldout = tuple(
            e
            for e in self.data.examples
            if self.split.partition(e.source.family_id) == "heldout"
        )
        self.assertTrue(self.train and self.heldout)
        self.candidates = (b"synthetic-candidate-A", b"synthetic-candidate-B")
        self.ordered = sorted(self.candidates, key=digest)
        self.clock = legacy.VirtualClock()
        deny = patch.object(
            socket, "socket", side_effect=AssertionError("unexpected network")
        )
        self.socket = deny.start()
        self.addCleanup(deny.stop)
        self.addCleanup(self.socket.assert_not_called)

    def adapters(
        self, script: tuple[Any, ...] | None = None, calls: int = 200
    ) -> tuple[Any, Any]:
        if script is None:
            script = tuple(ev.Completion(b"wrong", 1, 1, True, 1) for _ in self.train)
            script += tuple(ev.Completion(e.gold, 1, 1, True, 1) for e in self.train)
        provider = legacy.ScriptedProvider(script, clock=self.clock)
        budget = legacy.Budget(ev.Limits(calls, 2000, 2000, 2000), clock_ms=self.clock)
        return provider, budget

    def search(self, provider: Any, budget: Any, **kwargs: Any) -> Any:
        return optimize(
            self.data,
            self.split,
            self.candidates,
            provider=provider,
            budget=budget,
            per_call=ev.Reservation(10, 10),
            **kwargs,
        )

    def test_selects_exact_bytes_from_training_only(self) -> None:
        provider, budget = self.adapters()
        result = self.search(provider, budget)
        self.assertEqual(
            (result.status, result.selected), ("selected", self.ordered[1])
        )
        self.assertEqual(result.selected_sha256, digest(result.selected))
        expected = [
            (digest(c), e.output.output_id) for c in self.ordered for e in self.train
        ]
        self.assertEqual(
            [(r.artifact_sha256, r.output_id) for r in provider.calls], expected
        )
        heldout = {e.output.output_id for e in self.heldout}
        self.assertFalse(heldout & {r.output_id for r in provider.calls})
        self.assertEqual(budget.snapshot()["calls"], len(expected))
        self.assertEqual(
            dict(result.candidate_scores),
            {digest(self.ordered[0]): 0, digest(self.ordered[1]): len(self.train)},
        )

    def test_same_budget_continues_into_locked_evaluation(self) -> None:
        training = tuple(
            ev.Completion(e.gold, 1, 1, True, 1)
            for _ in self.candidates
            for e in self.train
        )
        evaluation = tuple(
            ev.Completion(e.gold, 1, 1, True, 1) for _ in range(2) for e in self.heldout
        )
        provider, budget = self.adapters(
            training + evaluation, calls=len(training) + len(evaluation) - 1
        )
        selection = self.search(provider, budget)
        self.assertEqual(selection.status, "selected")
        config = ev.Configuration(
            budget.limits, ev.Reservation(10, 10), provider.sha256, 0, 0, 1, ()
        )
        experiment = ev.Experiment(
            self.split.dataset_sha256,
            self.split.sha256,
            b"baseline",
            digest(b"baseline"),
            selection.selected,
            selection.selected_sha256,
            config,
            config.sha256,
        )
        report = legacy.evaluate(
            self.data, self.split, experiment, provider=provider, budget=budget
        )
        self.assertEqual(report.reason, "calls_exhausted")
        self.assertEqual(len(provider.calls), budget.limits.calls)
        self.assertEqual(dict(report.usage)["calls"], len(provider.calls))

    def test_retry_and_unknown_usage_cannot_reset_shared_budget(self) -> None:
        script = (
            ev.Completion(None, 1, 1, True, 1, "timeout"),
            ev.Completion(self.train[0].gold, 1, 1, True, 1),
        )
        provider, budget = self.adapters(script, calls=2)
        result = self.search(provider, budget, max_attempts=2)
        self.assertEqual(result.reason, "calls_exhausted")
        self.assertIsNone(result.selected)
        self.assertEqual([r.attempt for r in provider.calls], [1, 2])
        provider, budget = self.adapters((ev.Completion(b"answer", None, None, True),))
        result = self.search(provider, budget)
        self.assertEqual(result.reason, "unknown_or_invalid_usage")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(budget.snapshot()["unresolved_calls"], 1)
        self.assertIsNone(result.selected)

    def test_cancellation_and_unknown_safety_stop_all_later_stages(self) -> None:
        provider, budget = self.adapters()
        budget.cancel()
        self.assertEqual(self.search(provider, budget).reason, "cancelled")
        self.assertEqual(provider.calls, [])
        provider, budget = self.adapters((ev.Completion(b"answer", 1, 1, None),))
        self.assertEqual(self.search(provider, budget).reason, "unknown_safety")
        self.assertEqual(len(provider.calls), 1)
        with self.assertRaises(ev.Halt):
            budget.reserve(ev.Reservation(1, 1))

    def test_unhashable_outcomes_block_and_poison_shared_ledger(self) -> None:
        outcomes: tuple[Any, ...] = ([], {}, None, "unsupported")
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                malformed: Any = outcome
                provider, budget = self.adapters(
                    (ev.Completion(b"answer", 1, 1, True, outcome=malformed),)
                )
                result = self.search(provider, budget)
                self.assertEqual(result.reason, "malformed_reply")
                self.assertIsNone(result.selected)
                self.assertEqual(len(provider.calls), 1)
                self.assertEqual(budget.snapshot()["unresolved_calls"], 1)
                with self.assertRaises(ev.Halt):
                    budget.reserve(ev.Reservation(1, 1))

    def test_altered_split_or_candidates_deny_before_provider(self) -> None:
        provider, budget = self.adapters()
        with self.assertRaises(ValueError):
            optimize(
                self.data,
                replace(self.split, sha256="0" * 64),
                self.candidates,
                provider=provider,
                budget=budget,
                per_call=ev.Reservation(10, 10),
            )
        with self.assertRaises(ValueError):
            optimize(
                self.data,
                self.split,
                (b"same", b"same"),
                provider=provider,
                budget=budget,
                per_call=ev.Reservation(10, 10),
            )
        self.assertEqual(provider.calls, [])

    def test_holdout_leak_mutation_is_observed_in_real_requests(self) -> None:
        source = (ROOT / "refinery/optimization.py").read_text()
        anchor = "train, _ = validate_dataset(dataset, split)"
        self.assertEqual(source.count(anchor), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutant.py"
            path.write_text(
                source.replace(anchor, "_, train = validate_dataset(dataset, split)")
            )
            spec = importlib.util.spec_from_file_location(
                "refinery.mutant_optimization", path
            )
            if spec is None or spec.loader is None:
                self.fail("missing mutation loader")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            self.addCleanup(sys.modules.pop, spec.name)
            spec.loader.exec_module(module)
            provider, budget = self.adapters(
                tuple(ev.Completion(b"wrong", 1, 1, True) for _ in range(200))
            )
            module.optimize(
                self.data,
                self.split,
                self.candidates,
                provider=provider,
                budget=budget,
                per_call=ev.Reservation(10, 10),
            )
            heldout = {e.output.output_id for e in self.heldout}
            self.assertTrue(
                heldout & {r.output_id for r in provider.calls},
                "mutant must produce independently observed forbidden holdout calls",
            )
