"""Independent byte/SQL observations, subprocess interruption and negative effects."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from refinery.durable_evaluation import SQLiteBudget, run
from refinery.evaluation import (
    Configuration,
    Experiment,
    Halt,
    Limits,
    Reservation,
    ScriptedProvider,
    VirtualClock,
    Completion,
)
from refinery.lineage import GoldRule, digest
from refinery.storage import Store, VERSION, pack, unpack
from refinery.serving import Approval, LocalServing, Policy, Registry
from test_lineage import fixture


def setup(store, *, suffix="", content=None, clock=None, script=None):
    triples = [
        fixture(
            str(i),
            family="family-" + str(i),
            source_id="source-" + str(i),
            output_id="output-" + str(i),
        )
        for i in range(12)
    ]
    sources, outputs, feedback = map(tuple, zip(*triples))
    actors = {digest(pack(f)): f.actor_id for f in feedback}
    snapshot = store.commit_lineage(
        VERSION,
        sources,
        outputs,
        feedback,
        [GoldRule("summary", "summary-v1", ("positive",))],
        seed="seed-2",
        collection_evidence="synthetic-consent",
        verify_collection=lambda sha, evidence: evidence == "synthetic-consent",
        verify_actor=lambda f: actors.get(digest(pack(f))),
    )
    data, split = store.lineage(snapshot)
    heldout = [
        e for e in data.examples if split.partition(e.source.family_id) == "heldout"
    ]
    provider = ScriptedProvider(
        script
        or tuple(Completion(e.gold, 1, 1, True, 1) for _ in range(2) for e in heldout),
        clock=clock or VirtualClock(),
    )
    config = Configuration(
        Limits(200, 2000, 2000, 2000),
        Reservation(10, 10),
        provider.sha256,
        1_000_000,
        0,
        1,
        (),
    )
    candidate = (
        content
        or json.dumps(
            {"format": "synthetic.map.v1", "responses": {"hello": "answer" + suffix}},
            sort_keys=True,
        ).encode()
    )
    exp = Experiment(
        split.dataset_sha256,
        split.sha256,
        b"baseline",
        digest(b"baseline"),
        candidate,
        digest(candidate),
        config,
        config.sha256,
    )
    return snapshot, exp, provider


def child(code, directory, expected=0):
    prefix = "import sys, socket; sys.path.insert(0,'tests'); socket.create_connection=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); "
    result = subprocess.run(
        [sys.executable, "-c", prefix + code, str(directory)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != expected:
        raise AssertionError(result.stdout + result.stderr)
    return result


class DurableBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="refinery-durable-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "refinery.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.network = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        ).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.network.assert_not_called)


class DurableTests(DurableBase):
    def test_lineage_exact_revision_quarantine_conflict_and_restart_bytes(self):
        source, output, feedback = fixture("one")
        newer = replace(output, revision="r2", content=b"new", sha256=digest(b"new"))
        bad = replace(
            feedback,
            feedback_id="bad",
            output_revision="r2",
            output_sha256=output.sha256,
        )
        conflicting = replace(
            feedback,
            feedback_id="conflicting",
            kind="correction",
            correction=b"corrected",
        )
        key = self.store.commit_lineage(
            VERSION,
            [source],
            [output, newer],
            [feedback, bad, conflicting],
            [GoldRule("summary", "summary-v1", ("positive", "correction"))],
            seed="seed-2",
            collection_evidence="consent",
            verify_collection=lambda *_: True,
            verify_actor=lambda f: f.actor_id,
        )
        data, split = self.store.lineage(key)
        self.assertEqual(data.examples, ())
        self.assertEqual(
            data.excluded_counts,
            {"conflicting_feedback": 2, "missing_or_changed_output": 1},
        )
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT payload,digest FROM objects WHERE kind='output'"
            ).fetchall()
            self.assertEqual(
                {unpack(p).content for p, _ in rows}, {output.content, b"new"}
            )
            self.assertTrue(all(digest(p) == sha for p, sha in rows))
        result = child(
            "from refinery.storage import Store; s=Store(sys.argv[1]+'/refinery.sqlite'); print(s.lineage('"
            + key
            + "')[0].excluded_counts)",
            self.directory,
        )
        self.assertIn("conflicting_feedback", result.stdout)
        moved = replace(source, revision="next", family_id="unrelated-family")
        with self.assertRaisesRegex(ValueError, "family rebinding"):
            self.store.commit_lineage(
                VERSION,
                [moved],
                [],
                [],
                [],
                seed="seed-2",
                collection_evidence="consent",
                verify_collection=lambda *_: True,
            )

    def test_feedback_id_cannot_rebind_across_committed_batches(self):
        source, output, feedback = fixture("x")
        kwargs = dict(
            seed="seed",
            collection_evidence="consent",
            verify_collection=lambda *_: True,
            verify_actor=lambda f: f.actor_id,
        )
        rules = [GoldRule("summary", "summary-v1", ("positive",))]
        old = self.store.commit_lineage(
            VERSION, [source], [output], [feedback], rules, **kwargs
        )
        newer = replace(output, revision="new", content=b"new", sha256=digest(b"new"))
        rebound = replace(feedback, output_revision="new", output_sha256=newer.sha256)
        key = self.store.commit_lineage(
            VERSION, [source], [newer], [rebound], rules, **kwargs
        )
        self.assertEqual(self.store.lineage(key)[0].examples, ())
        self.assertEqual(
            self.store.lineage(key)[0].excluded_counts, {"conflicting_feedback_id": 1}
        )
        self.assertEqual(len(self.store.lineage(old)[0].examples), 1)

    def test_consent_default_deny_unsupported_version_and_actor_not_authority(self):
        s, o, f = fixture("one")
        for api, verifier in (
            (VERSION, None),
            ("future", lambda *_: True),
            (VERSION, lambda *_: 1),
        ):
            with self.assertRaises(ValueError):
                self.store.commit_lineage(
                    api,
                    [s],
                    [o],
                    [f],
                    [],
                    seed="seed",
                    collection_evidence="claimed",
                    verify_collection=verifier,
                )
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0
        )
        key = self.store.commit_lineage(
            VERSION,
            [s],
            [o],
            [f],
            [],
            seed="seed",
            collection_evidence="consent",
            verify_collection=lambda *_: True,
        )
        self.assertEqual(
            self.store.lineage(key)[0].excluded_counts, {"unverified_actor": 1}
        )

    def test_feedback_commit_interruption_is_atomic_in_fresh_process(self):
        for boundary, retained in (
            ("before_feedback_commit", 0),
            ("after_feedback_commit", 1),
        ):
            directory = self.directory / boundary
            directory.mkdir()
            code = (
                "from refinery.storage import Store,VERSION; from refinery.lineage import GoldRule; from test_lineage import fixture; import os; s=Store(sys.argv[1]+'/refinery.sqlite'); a,b,c=fixture('x'); s.commit_lineage(VERSION,[a],[b],[c],[GoldRule('summary','summary-v1',('positive',))],seed='seed',collection_evidence='c',verify_collection=lambda *_:True,verify_actor=lambda f:f.actor_id,checkpoint=lambda point:os._exit(91) if point=='"
                + boundary
                + "' else None)"
            )
            child(code, directory, 91)
            with Store(directory / "refinery.sqlite") as fresh:
                self.assertEqual(
                    fresh.db.execute(
                        "SELECT count(*) FROM objects WHERE kind='lineage'"
                    ).fetchone()[0],
                    retained,
                )
                self.assertEqual(
                    fresh.db.execute(
                        "SELECT count(*) FROM objects WHERE kind='feedback'"
                    ).fetchone()[0],
                    retained,
                )

    def test_durable_evaluation_replay_uses_immutable_report_without_calls(self):
        snapshot, exp, provider = setup(self.store)
        report = run(
            self.store,
            VERSION,
            "experiment",
            snapshot,
            exp,
            provider=provider,
            budget_id="budget",
        )
        self.assertEqual(report.status, "passed")
        self.assertEqual(len(provider.calls), len(report.attempts))
        self.assertEqual(
            [r.output_id for r in provider.calls if r.stage == "baseline"],
            [r.output_id for r in provider.calls if r.stage == "candidate"],
        )
        with Store(self.path) as fresh:
            replay_provider = ScriptedProvider(provider.script, clock=VirtualClock())
            replay = run(
                fresh,
                VERSION,
                "experiment",
                snapshot,
                exp,
                provider=replay_provider,
                budget_id="budget",
            )
            self.assertEqual(report, replay)
            self.assertEqual(replay_provider.calls, [])
            rows = fresh.db.execute(
                "SELECT outcome,receipt FROM attempts ORDER BY ticket"
            ).fetchall()
            self.assertEqual(len(rows), len(report.attempts))
            self.assertTrue(
                all(
                    state == "settled" and unpack(receipt).tokens == 1
                    for state, receipt in rows
                )
            )
        with self.assertRaisesRegex(ValueError, "conflict"):
            run(
                self.store,
                VERSION,
                "experiment",
                snapshot,
                replace(exp, candidate=b"changed", candidate_sha256=digest(b"changed")),
                provider=provider,
                budget_id="budget",
            )

    def test_interrupted_reservation_retains_budget_and_never_replays(self):
        code = "from test_durable import setup; from refinery.storage import Store,VERSION; from refinery.durable_evaluation import run; import os; s=Store(sys.argv[1]+'/refinery.sqlite'); snap,exp,p=setup(s); run(s,VERSION,'interrupted',snap,exp,provider=p,budget_id='shared',checkpoint=lambda point:os._exit(92) if point=='after_budget_reservation' else None)"
        child(code, self.directory, 92)
        snapshot, exp, provider = setup(self.store)
        with self.assertRaisesRegex(Halt, "interrupted_or_owned"):
            run(
                self.store,
                VERSION,
                "interrupted",
                snapshot,
                exp,
                provider=provider,
                budget_id="shared",
            )
        self.assertEqual(provider.calls, [])
        budget = SQLiteBudget(
            self.store, "shared", exp.configuration.limits, clock_ms=provider.clock
        )
        self.assertEqual(budget.snapshot()["unresolved_calls"], 1)
        self.assertEqual(budget.snapshot()["calls"], 1)
        self.assertIsNone(budget.snapshot()["cost_microunits"])
        with self.assertRaisesRegex(Halt, "unresolved_usage"):
            budget.checked_snapshot()

    def test_multi_connection_budget_concurrency_and_no_reset(self):
        limits = Limits(50, 20, 100, 20)
        SQLiteBudget(self.store, "shared", limits, clock_ms=lambda: 10)

        def reserve(_):
            with Store(self.path) as s:
                b = SQLiteBudget(s, "shared", limits, clock_ms=lambda: 10)
                try:
                    return b.reserve(Reservation(10, 10))
                except Halt:
                    return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(reserve, range(16)))
        self.assertEqual(sum(r is not None for r in replies), 2)
        b = SQLiteBudget(self.store, "shared", limits, clock_ms=lambda: 10)
        self.assertEqual(b.snapshot()["calls"], 2)
        self.assertEqual(b.snapshot()["unresolved_calls"], 2)
        with self.assertRaises(ValueError):
            SQLiteBudget(
                self.store, "shared", replace(limits, calls=100), clock_ms=lambda: 10
            )

    def test_unknown_usage_cancellation_expired_clock_and_split_mutation(self):
        snapshot, exp, provider = setup(
            self.store, script=(Completion(b"x", None, None, True),)
        )
        report = run(
            self.store,
            VERSION,
            "unknown",
            snapshot,
            exp,
            provider=provider,
            budget_id="unknown",
        )
        self.assertEqual(report.reason, "unknown_or_invalid_usage")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(dict(report.usage)["unresolved_calls"], 1)
        b = SQLiteBudget(
            self.store, "cancel", exp.configuration.limits, clock_ms=lambda: 10
        )
        b.cancel()
        with self.assertRaisesRegex(Halt, "cancelled"):
            SQLiteBudget(
                self.store, "cancel", exp.configuration.limits, clock_ms=lambda: 10
            ).reserve(Reservation(1, 1))
        SQLiteBudget(self.store, "expired", Limits(1, 1, 2, 1), clock_ms=lambda: 10)
        with self.assertRaisesRegex(Halt, "time_exhausted"):
            SQLiteBudget(
                self.store, "expired", Limits(1, 1, 2, 1), clock_ms=lambda: 12
            ).reserve(Reservation(1, 1))


class ServingTests(DurableBase):
    # Inherit the same network-denial/disposable-store fixture, without repeating tests.
    def candidate(self, name="one"):
        snapshot, exp, provider = setup(self.store, suffix=name)
        run(
            self.store,
            VERSION,
            name,
            snapshot,
            exp,
            provider=provider,
            budget_id="budget-" + name,
        )
        registry = Registry(
            self.store,
            verify_approval=lambda approval: (
                "reviewer" if approval.evidence == "independent-proof" else None
            ),
        )
        key = registry.qualify(VERSION, name, Policy("local", 1_000_000, 0, 2000, 2000))
        value = self.store.get("candidate", key)
        approval = Approval(
            value["candidate_sha256"],
            value["evaluation_sha256"],
            value["policy_sha256"],
            "local",
            "reviewer",
            "independent-proof",
        )
        return registry, key, approval

    def adapter(self):
        adapter = LocalServing(self.directory / "serving.sqlite")
        self.addCleanup(adapter.close)
        return adapter

    def test_exact_approval_default_deny_forgery_and_changed_target_zero_loads(self):
        reg, key, approval = self.candidate()
        adapter = self.adapter()
        for owner, grant in (
            (Registry(self.store), approval),
            (reg, replace(approval, actor="forged")),
            (reg, replace(approval, evidence="claimed")),
            (reg, replace(approval, environment="other")),
            (reg, replace(approval, candidate_sha256="a" * 64)),
        ):
            with self.assertRaises(ValueError):
                owner.begin(VERSION, "bad", key, grant, expected_generation=0)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )
        self.assertIsNone(adapter.readback("local"))

    def test_concurrent_promotion_compare_and_swap_has_one_owner(self):
        reg, key, approval = self.candidate()

        def begin(number):
            with Store(self.path) as other:
                owner = Registry(other, verify_approval=lambda _: "reviewer")
                try:
                    return owner.begin(
                        VERSION,
                        "owner-" + str(number),
                        key,
                        approval,
                        expected_generation=0,
                    )
                except ValueError:
                    return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(begin, range(4)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM activations").fetchone()[0], 1
        )
        self.assertEqual(reg.state("local")["generation"], 1)

    def test_before_load_process_death_fences_retry_without_effect(self):
        reg, key, approval = self.candidate()
        reg.begin(VERSION, "activation", key, approval, expected_generation=0)
        child(
            "from refinery.storage import Store,VERSION; from refinery.serving import Registry,LocalServing; import os; s=Store(sys.argv[1]+'/refinery.sqlite'); a=LocalServing(sys.argv[1]+'/serving.sqlite'); Registry(s).load(VERSION,'activation',a,checkpoint=lambda point:os._exit(94) if point=='before_candidate_load' else None)",
            self.directory,
            94,
        )
        adapter = self.adapter()
        self.assertEqual(reg.load(VERSION, "activation", adapter), "unknown")
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )
        self.assertIsNone(adapter.readback("local"))

    def test_load_readback_actual_output_and_failed_load_preserves_prior(self):
        reg, key, approval = self.candidate()
        adapter = self.adapter()
        reg.begin(VERSION, "activate-one", key, approval, expected_generation=0)
        self.assertIsNone(adapter.readback("local"))
        self.assertEqual(reg.load(VERSION, "activate-one", adapter), "active")
        first = adapter.predict("local", "hello")
        self.assertEqual(
            first,
            {"output": "answerone", "candidate_sha256": approval.candidate_sha256},
        )
        _, second, grant = self.candidate("two")
        reg.begin(VERSION, "failed-two", second, grant, expected_generation=1)

        class Reject:
            def load(self, *_):
                return False

            def readback(self, environment):
                return adapter.readback(environment)

        self.assertEqual(reg.load(VERSION, "failed-two", Reject()), "failed")
        self.assertEqual(adapter.predict("local", "hello"), first)
        self.assertEqual(reg.state("local")["active"], key)

    def test_fresh_process_lost_activation_ack_reconciles_without_reload(self):
        reg, key, approval = self.candidate()
        reg.begin(VERSION, "activation", key, approval, expected_generation=0)
        child(
            "from refinery.storage import Store,VERSION; from refinery.serving import Registry,LocalServing; import os; s=Store(sys.argv[1]+'/refinery.sqlite'); a=LocalServing(sys.argv[1]+'/serving.sqlite'); Registry(s).load(VERSION,'activation',a,checkpoint=lambda point:os._exit(93) if point=='after_candidate_load' else None)",
            self.directory,
            93,
        )
        self.assertIsNone(reg.state("local")["active"])
        adapter = self.adapter()
        self.assertEqual(
            adapter.predict("local", "hello")["candidate_sha256"],
            approval.candidate_sha256,
        )
        self.assertEqual(reg.load(VERSION, "activation", adapter), "active")
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 1
        )

    def test_before_load_unknown_conflicting_readback_and_concurrent_owner(self):
        reg, key, approval = self.candidate()
        reg.begin(VERSION, "activation", key, approval, expected_generation=0)
        adapter = self.adapter()
        with self.assertRaises(ValueError):
            reg.begin(VERSION, "concurrent", key, approval, expected_generation=0)

        def die(_):
            raise SystemExit(94)

        with self.assertRaises(SystemExit):
            reg.load(VERSION, "activation", adapter, checkpoint=die)
        self.assertEqual(reg.load(VERSION, "activation", adapter), "unknown")
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0
        )

        class Conflict:
            def readback(self, _):
                return {
                    "activation_id": "other",
                    "generation": 1,
                    "candidate_sha256": approval.candidate_sha256,
                }

        self.assertEqual(reg.reconcile(VERSION, "activation", Conflict()), "unknown")
        self.assertEqual(reg.state("local")["pending"], "activation")

    def test_rollback_exact_bytes_absent_history_repeated_and_unavailable(self):
        reg, first, approval = self.candidate()
        adapter = self.adapter()
        with self.assertRaises(ValueError):
            reg.rollback(VERSION, "absent", "local", expected_generation=0)
        reg.begin(VERSION, "first", first, approval, expected_generation=0)
        reg.load(VERSION, "first", adapter)
        with self.assertRaisesRegex(ValueError, "no approved predecessor"):
            reg.rollback(VERSION, "no-predecessor", "local", expected_generation=1)
        _, second, grant = self.candidate("two")
        reg.begin(VERSION, "second", second, grant, expected_generation=1)
        reg.load(VERSION, "second", adapter)
        old_output = adapter.predict("local", "hello")
        reg.rollback(VERSION, "rollback", "local", expected_generation=2)
        self.assertEqual(reg.load(VERSION, "rollback", adapter), "active")
        self.assertEqual(adapter.predict("local", "hello")["output"], "answerone")
        self.assertEqual(old_output["candidate_sha256"], grant.candidate_sha256)
        with self.assertRaises(ValueError):
            reg.rollback(VERSION, "rollback", "local", expected_generation=2)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 3
        )
        # Read failure is an unavailable artifact; no load can be invoked.
        with patch.object(
            self.store, "get", side_effect=ValueError("unavailable bytes")
        ):
            with self.assertRaises(ValueError):
                reg.rollback(VERSION, "missing", "local", expected_generation=3)
        self.assertEqual(
            adapter.db.execute("SELECT count(*) FROM calls").fetchone()[0], 3
        )


if __name__ == "__main__":
    unittest.main()
