"""Offline real runner + scripted provider. Independent order/forbidden-call checks."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import socket
import unittest
from unittest.mock import patch

from refinery.evaluation import (
    Budget, Completion, Configuration, Experiment, Halt, Limits, Reservation,
    ScriptedProvider, VirtualClock, evaluate,
)
from refinery.lineage import Dataset, GoldRule, build_dataset, digest, lock_split
from test_lineage import fixture


def dataset_fixture():
    triples = [fixture(str(i), family="family-" + str(i), source_id="source-" + str(i),
                       output_id="output-" + str(i)) for i in range(12)]
    sources, outputs, feedback = map(tuple, zip(*triples))
    data = build_dataset(sources, outputs, feedback, [GoldRule("summary", "summary-v1", ("positive",))],
                         verify_actor=lambda f: f.actor_id)
    split = lock_split(data, seed="seed-2")
    assert {p for _, p in split.assignments} == {"train", "heldout"}
    return data, split


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        deny = patch.object(socket, "socket", side_effect=AssertionError("network forbidden"))
        self.socket = deny.start()
        self.addCleanup(deny.stop)
        self.addCleanup(self.socket.assert_not_called)
        self.data, self.split = dataset_fixture()
        self.heldout = tuple(e for e in self.data.examples if self.split.partition(e.source.family_id) == "heldout")
        self.train = tuple(e for e in self.data.examples if self.split.partition(e.source.family_id) == "train")
        self.clock = VirtualClock()

    def setup_run(self, *, script=None, preparation=(), limits=None, max_attempts=1,
                  min_score_ppm=1_000_000, min_improvement_ppm=0):
        if script is None:
            script = tuple(Completion(b"scripted preparation", 1, 2, True, 1)
                           for _ in preparation for _ in self.train)
            script += tuple(Completion(b"wrong baseline", 1, 2, True, 1) for _ in self.heldout)
            script += tuple(Completion(e.gold, 1, 2, True, 1) for e in self.heldout)
        self.provider = ScriptedProvider(script, clock=self.clock)
        limits = limits or Limits(200, 2000, 2000, 2000)
        self.config = Configuration(limits, Reservation(10, 10), self.provider.sha256,
                                    min_score_ppm, min_improvement_ppm, max_attempts, preparation)
        self.experiment = Experiment(self.split.dataset_sha256, self.split.sha256,
                                     b"baseline-artifact", digest(b"baseline-artifact"),
                                     b"candidate-artifact", digest(b"candidate-artifact"),
                                     self.config, self.config.sha256)
        self.budget = Budget(limits, clock_ms=self.clock)
        return self

    def run_eval(self, **changes):
        args = dict(dataset=self.data, split=self.split, experiment=self.experiment,
                    provider=self.provider, budget=self.budget)
        args.update(changes)
        return evaluate(**args)

    def test_pass_reports_exact_boundaries_and_identical_locked_cases(self):
        self.setup_run(preparation=("optimization", "reflection"))
        report = self.run_eval()
        self.assertEqual((report.status, report.reason, report.baseline_score, report.candidate_score),
                         ("passed", None, 0, 1))
        expected = [(s, e.output.output_id, e.output.revision) for s in ("optimization", "reflection") for e in self.train]
        expected += [(s, e.output.output_id, e.output.revision) for s in ("baseline", "candidate") for e in self.heldout]
        actual = [(r.stage, r.output_id, r.output_revision) for r in self.provider.calls]
        self.assertEqual(actual, expected)
        self.assertEqual(report.heldout_cases, tuple((e.output.output_id, e.output.revision) for e in self.heldout))
        self.assertTrue(all(r.split_sha256 == self.split.sha256 for r in self.provider.calls))
        self.assertEqual(dict(report.usage), {"calls": len(expected), "tokens": len(expected),
                         "cost_microunits": 2 * len(expected), "elapsed_ms": len(expected),
                         "reason": None, "unresolved_calls": 0})
        self.assertEqual(report.baseline_metrics.calls, len(self.heldout))
        self.assertEqual(report.candidate_metrics.cost_microunits, 2 * len(self.heldout))
        self.assertEqual(report.candidate_metrics.latency_ms, len(self.heldout))
        self.assertEqual(report.config_sha256, digest(self.config.content))
        self.assertEqual(report.candidate_sha256, digest(self.experiment.candidate))

    def test_reproducible_reset_has_identical_report(self):
        self.setup_run()
        first = self.run_eval()
        self.clock = VirtualClock()
        self.setup_run()
        self.assertEqual(first, self.run_eval())

    def test_calls_never_reset_between_stages(self):
        self.setup_run(preparation=("optimization", "reflection"), limits=Limits(2, 1000, 1000, 1000))
        report = self.run_eval()
        self.assertEqual((report.status, report.reason), ("blocked", "calls_exhausted"))
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual([r.stage for r in self.provider.calls], ["optimization"] * 2)
        self.assertEqual(dict(report.usage)["calls"], 2)

    def test_budget_is_shared_across_retries(self):
        self.setup_run(script=(Completion(None, 2, 3, True, 1, "timeout"),) * 3,
                       limits=Limits(2, 1000, 1000, 1000), max_attempts=3)
        report = self.run_eval()
        self.assertEqual(report.reason, "calls_exhausted")
        self.assertEqual([r.attempt for r in self.provider.calls], [1, 2])
        self.assertEqual(dict(report.usage)["tokens"], 4)
        self.assertEqual(dict(report.usage)["cost_microunits"], 6)

    def test_timeout_exhaustion_and_script_error_do_not_blindly_continue(self):
        for script, reason in [((Completion(None, 1, 2, True, 1, "timeout"),), "timeout"),
                               ((), "provider_unknown")]:
            with self.subTest(reason=reason):
                self.setup_run(script=script)
                report = self.run_eval()
                self.assertEqual(report.reason, reason)
                self.assertEqual(len(self.provider.calls), 1)
                self.assertNotEqual(report.status, "passed")

    def test_unknown_cost_tokens_safety_stop_downstream_calls(self):
        for reply, reason in [(Completion(b"x", 1, None, True), "unknown_or_invalid_usage"),
                              (Completion(b"x", None, 1, True), "unknown_or_invalid_usage"),
                              (Completion(b"x", 1, 1, None), "unknown_safety"),
                              (Completion(b"x", 1, 1, False), "unsafe")]:
            with self.subTest(reason=reason):
                self.setup_run(script=(reply,))
                report = self.run_eval()
                self.assertEqual(report.reason, reason)
                self.assertEqual(len(self.provider.calls), 1)
                self.assertNotEqual(report.status, "passed")
                if reply.cost_microunits is None or reply.tokens is None:
                    self.assertIsNone(dict(report.usage)["cost_microunits"])
                    self.assertIsNone(dict(report.usage)["tokens"])

    def test_reservation_overrun_malformed_usage_and_response_stop(self):
        for reply in [Completion(b"x", 11, 1, True), Completion(b"x", 1, 11, True),
                      Completion(b"x", True, 1, True), Completion(b"x", -1, 1, True),
                      Completion("not-bytes", 1, 1, True), Completion(b"x", 1, 1, "yes")]:
            with self.subTest(reply=reply):
                self.setup_run(script=(reply,))
                report = self.run_eval()
                self.assertEqual(report.status, "blocked")
                self.assertEqual(len(self.provider.calls), 1)
                self.assertEqual(dict(report.usage)["unresolved_calls"], 1)

    def test_capacity_rejected_before_provider_call(self):
        for limits, reason in [(Limits(0, 100, 100, 100), "calls_exhausted"),
                               (Limits(10, 9, 100, 100), "tokens_exhausted"),
                               (Limits(10, 100, 100, 9), "cost_exhausted"),
                               (Limits(10, 100, 0, 100), "time_exhausted")]:
            with self.subTest(reason=reason):
                self.setup_run(limits=limits)
                report = self.run_eval()
                self.assertEqual(report.reason, reason)
                self.assertEqual(self.provider.calls, [])

    def test_known_usage_settles_and_releases_unused_reservation(self):
        budget = Budget(Limits(2, 11, 100, 11), clock_ms=self.clock)
        first = budget.reserve(Reservation(10, 10))
        budget.settle(first, tokens=1, cost_microunits=1)
        second = budget.reserve(Reservation(10, 10))
        budget.settle(second, tokens=10, cost_microunits=10)
        self.assertEqual((budget.calls, budget.tokens, budget.cost_microunits), (2, 11, 11))
        with self.assertRaises(Halt):
            budget.reserve(Reservation(0, 0))
        with self.assertRaises(ValueError):
            budget.settle(first, tokens=0, cost_microunits=0)

    def test_concurrent_reservations_cannot_oversubscribe(self):
        budget = Budget(Limits(50, 20, 100, 20), clock_ms=self.clock)
        def reserve(_):
            try:
                return budget.reserve(Reservation(10, 10))
            except Halt:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(16)))
        self.assertEqual(sum(r is not None for r in results), 2)
        self.assertEqual(budget.calls, 2)
        self.assertEqual(budget.snapshot()["unresolved_calls"], 2)

    def test_unresolved_shared_reservation_cannot_pass_gate(self):
        self.setup_run()
        self.budget.reserve(Reservation(1, 1))
        report = self.run_eval()
        self.assertEqual((report.status, report.reason), ("blocked", "unresolved_usage"))
        self.assertIsNone(dict(report.usage)["tokens"])
        self.assertIsNone(dict(report.usage)["cost_microunits"])
        self.assertEqual(dict(report.usage)["unresolved_calls"], 1)

    def test_completion_snapshot_is_atomic_with_later_cancellation(self):
        self.setup_run()
        original = self.budget.checked_snapshot
        def complete_then_cancel():
            snapshot = original()
            self.budget.cancel()  # after completion, must not rewrite this report
            return snapshot
        with patch.object(self.budget, "checked_snapshot", side_effect=complete_then_cancel):
            report = self.run_eval()
        self.assertEqual(report.status, "passed")
        self.assertIsNone(dict(report.usage)["reason"])
        self.assertEqual(dict(report.usage)["unresolved_calls"], 0)
        self.assertEqual(self.budget.reason, "cancelled")

    def test_cancel_before_dispatch_and_inflight_charges_usage(self):
        self.setup_run()
        self.budget.cancel()
        self.assertEqual(self.run_eval().reason, "cancelled")
        self.assertEqual(self.provider.calls, [])
        self.setup_run()
        original = self.provider.complete
        def finish_and_cancel(request):
            result = original(request)
            self.budget.cancel()
            return result
        with patch.object(self.provider, "complete", side_effect=finish_and_cancel):
            report = self.run_eval()
        self.assertEqual(report.reason, "cancelled")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual((dict(report.usage)["tokens"], dict(report.usage)["cost_microunits"]), (1, 2))
        self.assertEqual(dict(report.usage)["elapsed_ms"], 1)

    def test_elapsed_time_all_stages_and_deadline_boundary(self):
        self.setup_run(preparation=("optimization",), limits=Limits(100, 1000, 2, 1000))
        report = self.run_eval()
        self.assertEqual(report.reason, "time_exhausted")
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(dict(report.usage)["elapsed_ms"], 2)
        self.assertEqual(dict(report.usage)["tokens"], 2)

    def test_clock_regression_blocks_before_call(self):
        self.clock.advance(1)
        self.setup_run()
        self.clock.now = 0
        report = self.run_eval()
        self.assertEqual(report.reason, "invalid_clock")
        self.assertEqual(self.provider.calls, [])

    def test_empty_evaluation_blocks_and_no_provider_calls(self):
        self.setup_run()
        empty = Dataset((), (), 0, 0)
        split = lock_split(empty, seed="seed-1")
        experiment = replace(self.experiment, dataset_sha256=split.dataset_sha256, split_sha256=split.sha256)
        report = self.run_eval(dataset=empty, split=split, experiment=experiment)
        self.assertEqual(report.reason, "empty_evaluation")
        self.assertEqual(self.provider.calls, [])

    def test_deliberate_leakage_and_dataset_mutations_fail_before_call(self):
        self.setup_run()
        family, part = self.split.assignments[0]
        bad_split = replace(self.split, assignments=((family, "heldout" if part == "train" else "train"),) + self.split.assignments[1:])
        changed_gold = replace(self.data.examples[0], gold=b"deliberately mutated gold")
        for kwargs in [dict(split=bad_split), dict(dataset=replace(self.data, examples=(changed_gold,) + self.data.examples[1:]))]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_eval(**kwargs)
        self.assertEqual(self.provider.calls, [])

    def test_actual_artifact_bytes_and_all_effective_config_are_hash_locked(self):
        self.setup_run()
        for change in [dict(candidate=b"changed"), dict(baseline=b"changed"),
                       dict(configuration=replace(self.config, min_score_ppm=0)),
                       dict(configuration=replace(self.config, max_attempts=2))]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.experiment, **change)
        other = replace(self.experiment, configuration=replace(self.config, limits=Limits(1, 10, 10, 10)),
                        config_sha256=replace(self.config, limits=Limits(1, 10, 10, 10)).sha256)
        with self.assertRaises(ValueError):
            self.run_eval(experiment=other)
        self.provider.script += (Completion(b"x", 1, 1, True),)
        with self.assertRaises(ValueError):
            self.run_eval()
        self.assertEqual(self.provider.calls, [])

    def test_quality_gate_does_not_assume_exact_string_is_general_quality(self):
        self.setup_run(script=tuple(Completion(b"wrong", 1, 2, True) for _ in range(2 * len(self.heldout))))
        report = self.run_eval()
        self.assertEqual(report.reason, "threshold_failed")
        self.assertEqual(report.candidate_score, 0)
        self.assertEqual(self.config.metric, "synthetic_exact_match.v1")
        from refinery import activate
        with self.assertRaises(NotImplementedError):
            activate()

    def test_invalid_types_integers_and_bounds_reject(self):
        for bad in [True, -1, 1.5, float("inf"), 2**64]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Limits(bad, 1, 1, 1)
        self.setup_run()
        for change in [dict(max_attempts=0), dict(min_score_ppm=-1), dict(min_score_ppm=True),
                       dict(preparation=("candidate",)), dict(currency="USD"), dict(cost_scale=1_000_000.0), dict(cost_scale=True), dict(metric="real-quality")]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.config, **change)


if __name__ == "__main__":
    unittest.main()
