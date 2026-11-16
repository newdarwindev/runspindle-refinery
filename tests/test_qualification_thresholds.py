"""Exact qualification arithmetic over real offline evaluation and SQLite records."""

from __future__ import annotations

import importlib
import inspect
import math
import socket
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

ev: Any = importlib.import_module("refinery.evaluation")
de: Any = importlib.import_module("refinery.durable_evaluation")
lin: Any = importlib.import_module("refinery.lineage")
st: Any = importlib.import_module("refinery.storage")
serving: Any = importlib.import_module("refinery.serving")


def evaluated(
    store: Any,
    count: int,
    baseline: int,
    candidate: int,
    *,
    thresholds: tuple[int, int] = (0, 0),
    prepared_retry: bool = False,
) -> tuple[Any, Any]:
    """Run the actual offline evaluator; only feedback authorities are synthetic."""
    sources, outputs, feedback = [], [], []
    needed = {"heldout": count, "train": int(prepared_retry)}
    for number in range(10000):
        family = f"boundary-family-{number}"
        role = (
            "heldout"
            if int(lin._json_digest(["boundary-seed", family]), 16) % 10 < 2
            else "train"
        )
        if needed[role] == 0:
            continue
        needed[role] -= 1
        source = lin.SourceRevision(
            f"source-{number}",
            "1",
            family,
            b"synthetic input",
            lin.digest(b"synthetic input"),
        )
        gold = f"synthetic gold {number}".encode()
        output = lin.OutputRevision(
            f"output-{number}",
            "1",
            "summary",
            source.source_id,
            "1",
            source.sha256,
            gold,
            lin.digest(gold),
        )
        observed = lin.Feedback(
            f"feedback-{number}",
            "synthetic-actor",
            output.output_id,
            "1",
            output.sha256,
            "summary-v1",
            "positive",
        )
        sources.append(source)
        outputs.append(output)
        feedback.append(observed)
        if not any(needed.values()):
            break
    if any(needed.values()):
        raise AssertionError("bounded fixture construction exhausted")
    snapshot = store.commit_lineage(
        st.VERSION,
        sources,
        outputs,
        feedback,
        (lin.GoldRule("summary", "summary-v1", ("positive",)),),
        seed="boundary-seed",
        collection_evidence="synthetic-consent",
        verify_collection=lambda *_: True,
        verify_actor=lambda f: f.actor_id,
    )
    data, split = store.lineage(snapshot)
    heldout = [
        e for e in data.examples if split.partition(e.source.family_id) == "heldout"
    ]
    train = [e for e in data.examples if split.partition(e.source.family_id) == "train"]
    replies = [ev.Completion(e.gold, 1, 1, True, 1) for _ in range(2) for e in train]
    if prepared_retry:
        replies.append(ev.Completion(None, 1, 1, True, 1, "timeout"))
    replies.extend(
        ev.Completion(e.gold if i < correct else b"wrong", 1, 1, True, 1)
        for correct in (baseline, candidate)
        for i, e in enumerate(heldout)
    )
    provider = ev.ScriptedProvider(tuple(replies), clock=ev.VirtualClock())
    config = ev.Configuration(
        ev.Limits(2000, 20000, 20000, 20000),
        ev.Reservation(10, 10),
        provider.sha256,
        *thresholds,
        2 if prepared_retry else 1,
        ("optimization", "reflection") if prepared_retry else (),
    )
    content = b'{"format":"synthetic.map.v1","responses":{"hello":"answer"}}'
    experiment = ev.Experiment(
        split.dataset_sha256,
        split.sha256,
        b"baseline",
        lin.digest(b"baseline"),
        content,
        lin.digest(content),
        config,
        config.sha256,
    )
    report = de.run(
        store,
        st.VERSION,
        "evaluation",
        snapshot,
        experiment,
        provider=provider,
        budget_id="budget",
        synthetic_fixture=True,
    )
    if report.status != "passed":
        raise AssertionError(f"fixture evaluation blocked: {report.reason}")
    return report, provider


class QualificationThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        network = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(spy.assert_not_called)
        loader = patch.object(
            serving.LocalServing,
            "load",
            side_effect=AssertionError("activation forbidden"),
        )
        load_spy = loader.start()
        self.addCleanup(loader.stop)
        self.addCleanup(load_spy.assert_not_called)
        self.approval = Mock(side_effect=AssertionError("approval forbidden"))
        self.addCleanup(self.approval.assert_not_called)
        self.store = st.Store(":memory:")
        self.addCleanup(self.store.close)

    def registry(self, store: Any = None) -> Any:
        return serving.Registry(store or self.store, verify_approval=self.approval)

    def qualify(self, quality: int, improvement: int, *, store: Any = None) -> str:
        return str(
            self.registry(store).qualify(
                st.VERSION,
                "evaluation",
                serving.Policy("local", quality, improvement, 20000, 20000),
            )
        )

    def assert_empty_candidates(self, store: Any = None) -> None:
        db = (store or self.store).db
        self.assertEqual(
            db.execute(
                "SELECT count(*) FROM objects WHERE kind='candidate'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            db.execute("SELECT count(*) FROM activations").fetchone()[0], 0
        )
        self.assertEqual(
            db.execute("SELECT count(*) FROM environments").fetchone()[0], 0
        )

    def assert_denied_unchanged(self, quality: int, improvement: int) -> None:
        before = list(self.store.db.iterdump())
        with self.assertRaises(ValueError):
            self.qualify(quality, improvement)
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assert_empty_candidates()

    def test_exact_improvement_boundary_and_one_ppm_stricter_denials(self) -> None:
        report, provider = evaluated(self.store, 5, 1, 3, thresholds=(600000, 400000))
        self.assertEqual((report.baseline_score, report.candidate_score), (0.2, 0.6))
        self.assertEqual(len(provider.calls), 10)
        self.assert_denied_unchanged(600001, 400000)
        self.assert_denied_unchanged(600000, 400001)
        before = st.pack(report)
        key = self.qualify(600000, 400000)
        self.assertEqual(
            self.store.get("candidate", key)["evaluation_sha256"], lin.digest(before)
        )
        self.assertEqual(st.pack(report), before)
        self.qualify(599999, 399999)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM activations").fetchone()[0], 0
        )
        self.assertEqual(len(provider.calls), 10)

    def test_quality_boundary_with_eighty_cases_is_exact(self) -> None:
        report, provider = evaluated(self.store, 80, 0, 41, thresholds=(512500, 512500))
        self.assertEqual(report.candidate_score, 41 / 80)
        self.assert_denied_unchanged(512501, 0)
        self.qualify(512500, 512500)
        self.assertEqual(len(provider.calls), 160)

    def test_third_and_seventh_ratios_and_zero_improvement(self) -> None:
        for count, baseline, candidate, quality, improvement in (
            (3, 1, 2, 666666, 333333),
            (7, 2, 5, 714285, 428571),
            (5, 3, 3, 600000, 0),
        ):
            with self.subTest(count=count), st.Store(":memory:") as store:
                report, provider = evaluated(store, count, baseline, candidate)
                self.assertEqual(
                    serving._score_counts(report), (baseline, candidate, count)
                )
                self.qualify(quality, improvement, store=store)
                before = list(store.db.iterdump())
                with self.assertRaises(ValueError):
                    self.qualify(quality, improvement + 1, store=store)
                self.assertEqual(list(store.db.iterdump()), before)
                self.assertEqual(len(provider.calls), 2 * count)

    def test_preparation_and_timeout_do_not_change_denominator(self) -> None:
        report, provider = evaluated(self.store, 5, 1, 3, prepared_retry=True)
        self.assertEqual(len(report.attempts), 13)
        self.assertEqual(len(report.heldout_cases), 5)
        self.assertEqual(sum(a.outcome == "timeout" for a in report.attempts), 1)
        self.qualify(600000, 400000)
        self.assertEqual(len(provider.calls), 13)

    def test_invalid_scores_and_denominators_reject_without_writes(self) -> None:
        report, _ = evaluated(self.store, 5, 1, 3)
        invalid_scores = (
            None,
            True,
            False,
            0,
            1,
            "0.6",
            float("nan"),
            float("inf"),
            float("-inf"),
            -0.1,
            1.1,
            0.61,
            math.nextafter(0.6, 0.0),
            math.nextafter(0.6, 1.0),
        )
        invalid_cases = (
            (),
            list(report.heldout_cases),
            (report.heldout_cases[0],) * 5,
            tuple((f"case-{i}", "1") for i in range(1001)),
            (("case",),),
            (("case", "1", "extra"),),
            (["case", "1"],),
            (("case", True),),
            (("", "1"),),
            (("case\n", "1"),),
        )
        before = list(self.store.db.iterdump())
        for field in ("baseline_score", "candidate_score"):
            for value in invalid_scores:
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises(ValueError),
                ):
                    serving._score_counts(replace(report, **{field: value}))
        for invalid_case in invalid_cases:
            with self.subTest(cases=invalid_case), self.assertRaises(ValueError):
                serving._score_counts(replace(report, heldout_cases=invalid_case))
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assert_empty_candidates()

    def test_score_grid_bounds_preserve_canonical_floats(self) -> None:
        report, _ = evaluated(self.store, 5, 1, 3)
        for count in (1, 2, 3, 5, 7, 80, 999, 1000):
            cases = tuple((f"case-{i}", "1") for i in range(count))
            for correct in (0, 1, count // 2, max(0, count - 1), count):
                value = replace(
                    report,
                    heldout_cases=cases,
                    baseline_score=correct / count,
                    candidate_score=correct / count,
                )
                self.assertEqual(
                    serving._score_counts(value), (correct, correct, count)
                )

    def test_persisted_nongrid_score_is_rejected_before_candidate_write(self) -> None:
        report, _ = evaluated(self.store, 5, 1, 3)
        malformed = replace(report, candidate_score=0.61)
        sha = lin.digest(st.pack(malformed))
        with self.store.transaction() as tx:
            self.store.put(tx, "report", sha, malformed)
            tx.execute(
                "UPDATE experiments SET report=? WHERE identity='evaluation'", (sha,)
            )
        self.assert_denied_unchanged(600000, 400000)

    def test_negative_improvement_remains_rejected(self) -> None:
        report, _ = evaluated(self.store, 5, 1, 3)
        negative = replace(report, baseline_score=4 / 5)
        sha = lin.digest(st.pack(negative))
        with self.store.transaction() as tx:
            self.store.put(tx, "report", sha, negative)
            tx.execute(
                "UPDATE experiments SET report=? WHERE identity='evaluation'", (sha,)
            )
        self.assert_denied_unchanged(0, 0)

    def test_existing_report_bytes_survive_reopen_and_qualification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="refinery-threshold-") as temporary:
            path = Path(temporary) / "store.sqlite"
            with st.Store(path) as first:
                report, provider = evaluated(first, 5, 1, 3)
                encoded = st.pack(report)
                sha = lin.digest(encoded)
            with st.Store(path) as reopened:
                self.assertEqual(st.pack(reopened.get("report", sha)), encoded)
                key = self.qualify(600000, 400000, store=reopened)
                self.assertEqual(
                    reopened.get("candidate", key)["evaluation_sha256"], sha
                )
                self.assertEqual(st.pack(reopened.get("report", sha)), encoded)
                self.assertEqual(
                    reopened.db.execute("SELECT count(*) FROM activations").fetchone()[
                        0
                    ],
                    0,
                )
            self.assertEqual(len(provider.calls), 10)

    def test_float_comparison_mutation_fails_independent_boundary_oracle(self) -> None:
        evaluated(self.store, 5, 1, 3)
        original = textwrap.dedent(inspect.getsource(serving.Registry.qualify))
        before = "candidate_count * 1_000_000 < policy.min_quality_ppm * denominator"
        after = "report.candidate_score * 1_000_000 < policy.min_quality_ppm"
        self.assertEqual(original.count(before), 1)
        mutated = original.replace(before, after)
        before = "(candidate_count - baseline) * 1_000_000\n        < policy.min_improvement_ppm * denominator"
        after = "(report.candidate_score - report.baseline_score) * 1_000_000\n        < policy.min_improvement_ppm"
        self.assertEqual(mutated.count(before), 1)
        namespace = dict(vars(serving))
        exec(
            compile(
                mutated.replace(before, after), "<float-threshold-mutation>", "exec"
            ),
            namespace,
        )
        with patch.object(serving.Registry, "qualify", namespace["qualify"]):
            accepted = True
            try:
                self.qualify(600000, 400000)
            except ValueError:
                accepted = False
            with self.assertRaises(AssertionError):
                self.assertTrue(accepted, "exact-count boundary must qualify")
        self.assert_empty_candidates()


if __name__ == "__main__":
    unittest.main()
