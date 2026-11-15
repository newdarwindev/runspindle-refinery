"""Real SQLite lifecycle tests using explicit result fixtures, not a DSPy double.

The fixture calls the real offline dispatch transport, then commits a hand-built
candidate checkpoint. It does not execute or qualify BootstrapFewShot.
"""

from __future__ import annotations

import importlib
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

from refinery import dspy_optimizer as opt
from refinery import evaluation as ev
from refinery.durable_dspy import Session
from refinery.durable_evaluation import SQLiteBudget
from refinery.feedback_use import UseDenied
from refinery.lineage import digest
from refinery.optimization import validate_dataset

storage: Any = importlib.import_module("refinery.storage")
fixture: Any = importlib.import_module("test_durable")
legacy: Any = ev
ROOT = Path(__file__).resolve().parents[1]
LIMITS = ev.Limits(200, 2000, 2000, 2000)


def prepare(
    store: Any,
    *,
    identity: str = "dspy-run",
    clock: Any = None,
    checkpoint: Any = lambda _: None,
    budget_id: str = "shared",
) -> Session:
    snapshot, _, _ = fixture.setup(store)
    dataset, split = store.lineage(snapshot)
    train, heldout = validate_dataset(dataset, split)
    script = (ev.Completion(train[0].gold, 1, 1, True, 1),) + tuple(
        ev.Completion(e.gold, 1, 1, True, 1) for _ in range(2) for e in heldout
    )
    provider = legacy.ScriptedProvider(script, clock=clock or legacy.VirtualClock())
    return Session(
        store,
        storage.VERSION,
        identity,
        snapshot,
        provider=provider,
        limits=LIMITS,
        per_call=ev.Reservation(10, 10),
        budget_id=budget_id,
        settings=opt.Settings(1, 1),
        synthetic_fixture=True,
        checkpoint=checkpoint,
    )


def compile_fixture(session: Session) -> opt.Compiled:
    """Commit a labeled fixture of completed compilation via real storage methods."""
    session.claim_compile()
    example = session.train[0]
    dispatch = opt.Dispatch(
        session.train,
        session.split,
        stage="optimization",
        artifact_sha256=session.config,
        provider=session.provider,
        budget=session.budget(),
        per_call=session.per_call,
        check_use=lambda: session.guard("optimization"),
    )
    dispatch.invoke(lambda **_: dispatch.forward([]), opt.case(example))
    candidate = opt.encode_candidate(
        ((opt.case(example), example.gold.decode()),),
        session.train,
        session.split,
        session.config,
    )
    result = opt.Compiled(
        "compiled",
        None,
        session.config,
        candidate,
        digest(candidate),
        tuple(dispatch.attempts),
        tuple(sorted(session.budget().snapshot().items())),
    )
    session.commit_compiled(result)
    return result


def evaluate_fixture(session: Session) -> dict[str, Any]:
    compiled = session.claim_evaluation()
    _, heldout = validate_dataset(session.dataset, session.split)
    attempts: list[ev.Attempt] = []
    predictions = []
    for stage in ("baseline", "candidate"):
        sha = (
            digest(opt.canonical({"signature": opt.SIGNATURE, "demonstrations": []}))
            if stage == "baseline"
            else compiled.candidate_sha256
        )
        dispatch = opt.Dispatch(
            heldout,
            session.split,
            stage=stage,
            artifact_sha256=str(sha),
            provider=session.provider,
            budget=session.budget(),
            per_call=session.per_call,
            check_use=lambda: session.guard("evaluation"),
        )
        for example in heldout:
            dispatch.invoke(lambda **_: dispatch.forward([]), opt.case(example))
            predictions.append(
                (
                    stage,
                    example.output.output_id,
                    example.output.revision,
                    digest(example.gold),
                )
            )
        attempts.extend(dispatch.attempts)
    result = {
        "status": "evaluated",
        "scores": {"baseline": len(heldout), "candidate": len(heldout)},
        "attempts": tuple(attempts),
        "predictions": tuple(predictions),
        "candidate_sha256": compiled.candidate_sha256,
        "usage": session.budget().snapshot(),
        "quality": "synthetic_exact_match_only",
        "promotion": "not_authorized",
    }
    session.commit_evaluated(result)
    return result


class DurableDSPyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / "store.sqlite")
        denied = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = denied.start()
        self.addCleanup(denied.stop)
        self.addCleanup(spy.assert_not_called)

    def child(self, phase: str) -> subprocess.CompletedProcess[str]:
        script = """
import os, socket, sys
from unittest.mock import patch
from test_durable_dspy import prepare, compile_fixture, evaluate_fixture, storage
phase = sys.argv[2]
def checkpoint(point):
    if phase == point:
        os._exit(73)
with patch.object(socket, 'socket', side_effect=AssertionError('network')):
    with storage.Store(sys.argv[1]) as store:
        session = prepare(store, checkpoint=checkpoint)
        if phase == 'recover':
            with patch('refinery.dspy_optimizer.framework', side_effect=AssertionError('framework import')):
                result = session.evaluate()
            print(storage.pack(result).hex())
            print(len(session.provider.calls))
        else:
            compile_fixture(session)
            evaluate_fixture(session)
"""
        env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "tests"))
        return subprocess.run(
            [sys.executable, "-c", script, self.path, phase],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_completed_result_fresh_process_has_zero_calls_and_no_framework(
        self,
    ) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            compile_fixture(session)
            result = evaluate_fixture(session)
        fresh = self.child("recover")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(fresh.stdout.splitlines(), [storage.pack(result).hex(), "0"])

    def test_partial_checkpoint_is_not_full_completion_and_resumes_without_replay(
        self,
    ) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            compiled = compile_fixture(session)
        with storage.Store(self.path) as store:
            fresh = prepare(store)
            with patch(
                "refinery.dspy_optimizer.framework",
                side_effect=AssertionError("framework import"),
            ):
                self.assertEqual(fresh.compile(), compiled)
            self.assertIsNone(fresh.recover_evaluated())
            self.assertEqual(fresh.provider.calls, [])
            with self.assertRaisesRegex(ev.Halt, "invalid_clock"):
                fresh.claim_evaluation()
            fresh.provider.clock.now = 1
            with patch.object(
                ev.ScriptedProvider, "complete", wraps=fresh.provider.complete
            ) as spy:
                recovered = fresh.claim_evaluation()
                spy.assert_not_called()
            self.assertEqual(recovered, compiled)
            self.assertEqual(
                fresh.provider.calls, [a.request for a in compiled.attempts]
            )
            self.assertEqual(
                store.db.execute("SELECT state FROM experiments").fetchone()[0],
                "evaluating",
            )

    def test_process_interruptions_never_replay_uncertain_phases(self) -> None:
        for point in (
            "after_dspy_compile_claim",
            "after_budget_reservation",
            "before_dspy_compile_commit",
            "after_dspy_evaluation_claim",
            "before_dspy_evaluation_commit",
        ):
            with self.subTest(point=point):
                self.path += ".next"
                child = self.child(point)
                self.assertEqual(child.returncode, 73, child.stderr)
                with storage.Store(self.path) as store:
                    session = prepare(store)
                    with patch.object(
                        ev.ScriptedProvider,
                        "complete",
                        side_effect=AssertionError("replay"),
                    ) as spy:
                        with self.assertRaises(ev.Halt):
                            session.compile()
                        with self.assertRaises(ev.Halt):
                            session.evaluate()
                        spy.assert_not_called()
                    if point == "after_budget_reservation":
                        ledger: Any = SQLiteBudget(
                            store, "shared", LIMITS, clock_ms=legacy.VirtualClock()
                        )
                        self.assertEqual(ledger.snapshot()["unresolved_calls"], 1)

    def test_after_commit_interruption_recovers_only_committed_phase(self) -> None:
        for point in ("after_dspy_compile_commit", "after_dspy_evaluation_commit"):
            with self.subTest(point=point):
                self.path += ".next"
                child = self.child(point)
                self.assertEqual(child.returncode, 73, child.stderr)
                with storage.Store(self.path) as store:
                    session = prepare(store)
                    with patch(
                        "refinery.dspy_optimizer.framework",
                        side_effect=AssertionError("framework"),
                    ):
                        if point == "after_dspy_compile_commit":
                            self.assertEqual(session.compile().status, "compiled")
                            self.assertIsNone(session.recover_evaluated())
                        else:
                            self.assertEqual(session.evaluate()["status"], "evaluated")
                    self.assertEqual(session.provider.calls, [])

    def test_owner_race_has_one_claim_and_loser_no_ledger_reset(self) -> None:
        with storage.Store(self.path) as store:
            prepare(store)
        claimed, release = threading.Event(), threading.Event()

        def first() -> None:
            with storage.Store(self.path) as store:
                session = prepare(store)
                session.claim_compile()
                claimed.set()
                if not release.wait(10):
                    raise TimeoutError("race test")

        with ThreadPoolExecutor(max_workers=2) as pool:
            work = pool.submit(first)
            self.assertTrue(claimed.wait(10))
            try:
                with storage.Store(self.path) as store:
                    loser = prepare(store)
                    with self.assertRaises(ev.Halt):
                        loser.claim_compile()
                    self.assertEqual(loser.provider.calls, [])
                    self.assertEqual(
                        store.db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0],
                        1,
                    )
            finally:
                release.set()
            work.result(timeout=15)

    def test_shared_budget_conflict_rolls_back_enrollment_and_claim(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            SQLiteBudget(
                store, "shared", ev.Limits(1, 1, 1, 1), clock_ms=session.provider.clock
            )
            with self.assertRaises((ValueError, ev.Halt)):
                session.claim_compile()
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0
            )
            self.assertEqual(
                store.db.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind='dspy_partition'"
                ).fetchone()[0],
                0,
            )

    def test_global_family_seal_blocks_second_candidate_and_legacy_use(self) -> None:
        with storage.Store(self.path) as store:
            first = prepare(store)
            compile_fixture(first)
            second = prepare(store, identity="other-run", clock=first.provider.clock)
            compile_fixture(second)
            first.claim_evaluation()
            with self.assertRaises(UseDenied):
                second.claim_evaluation()
            third = prepare(store, identity="third-run", clock=first.provider.clock)
            with self.assertRaises(UseDenied):
                third.claim_compile()
            guard_module: Any = importlib.import_module("refinery.feedback_use")
            for purpose in ("optimization", "evaluation"):
                guard = guard_module.use_guard(
                    store,
                    first.snapshot_sha256,
                    purpose=purpose,
                    configuration_sha256="a" * 64,
                    run_sha256="b" * 64,
                    retained_use=None,
                    synthetic_fixture=True,
                )
                with self.assertRaises(UseDenied):
                    guard()
            self.assertEqual(
                second.provider.calls,
                [a.request for a in second.compiled()[0].attempts],
            )

    def test_enrollment_rejects_preexisting_heldout_exposure(self) -> None:
        with storage.Store(self.path) as store:
            snapshot, experiment, provider = fixture.setup(store)
            evaluator: Any = importlib.import_module("refinery.durable_evaluation")
            evaluator.run(
                store,
                storage.VERSION,
                "legacy",
                snapshot,
                experiment,
                provider=provider,
                budget_id="legacy-budget",
                synthetic_fixture=True,
            )
            session = prepare(store)
            with self.assertRaisesRegex(ev.Halt, "prior_heldout_exposure"):
                session.claim_compile()
            self.assertEqual(session.provider.calls, [])
            self.assertEqual(
                store.db.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind='dspy_partition'"
                ).fetchone()[0],
                0,
            )

    def test_snapshot_seed_cannot_reassign_enrolled_families(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            session.claim_compile()
            policy: Any = importlib.import_module("refinery.heldout_policy")
            lin: Any = importlib.import_module("refinery.lineage")
            changed = lin.lock_split(session.dataset, seed="different-seed")
            self.assertNotEqual(
                policy.partitions(session.dataset, session.split),
                policy.partitions(session.dataset, changed),
            )
            with self.assertRaisesRegex(ev.Halt, "locked_family_partition"):
                with store.transaction() as tx:
                    policy.enroll(
                        store,
                        tx,
                        session.dataset,
                        changed,
                        owner="new-owner",
                        configuration_sha256="c" * 64,
                    )

    def test_cached_result_or_receipt_tampering_fails_before_calls(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            compile_fixture(session)
            evaluate_fixture(session)
            store.db.execute("UPDATE experiments SET report=?", ("0" * 64,))
            fresh = prepare(store)
            with self.assertRaisesRegex(ValueError, "association"):
                fresh.recover_evaluated()
            self.assertEqual(fresh.provider.calls, [])

    def test_cancelled_reservation_persists_blocked_compilation_without_replay(
        self,
    ) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            session.claim_compile()
            budget = session.budget()
            budget.checkpoint = lambda _: budget.cancel()
            dispatch = opt.Dispatch(
                session.train,
                session.split,
                stage="optimization",
                artifact_sha256=session.config,
                provider=session.provider,
                budget=budget,
                per_call=session.per_call,
                check_use=lambda: session.guard("optimization"),
            )
            with self.assertRaisesRegex(ev.Halt, "cancelled"):
                dispatch.invoke(
                    lambda **_: dispatch.forward([]), opt.case(session.train[0])
                )
            result = opt.Compiled(
                "blocked",
                "cancelled",
                session.config,
                None,
                None,
                (),
                tuple(sorted(budget.snapshot().items())),
            )
            session.commit_compiled(result)
            self.assertEqual(session.compile(), result)
            with self.assertRaisesRegex(ev.Halt, "compilation_blocked"):
                session.evaluate()
            self.assertEqual(session.provider.calls, [])
            self.assertEqual(budget.snapshot()["unresolved_calls"], 1)

    def test_cancelled_evaluation_does_not_release_family_seal(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            compile_fixture(session)
            compiled = session.claim_evaluation()
            budget = session.budget()
            budget.cancel()
            report = {
                "status": "blocked",
                "scores": {},
                "attempts": (),
                "predictions": (),
                "candidate_sha256": compiled.candidate_sha256,
                "usage": budget.snapshot(),
            }
            session.commit_evaluated(report)
            fresh = prepare(store)
            self.assertEqual(fresh.evaluate(), report)
            with self.assertRaises(UseDenied):
                prepare(store, identity="new-after-cancel").claim_compile()

    def test_removed_policy_guard_mutation_exposes_legacy_heldout_dispatch(
        self,
    ) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            session.claim_compile()
            snapshot, experiment, provider = fixture.setup(store)
            evaluator: Any = importlib.import_module("refinery.durable_evaluation")
            with self.assertRaises(UseDenied):
                evaluator.run(
                    store,
                    storage.VERSION,
                    "legacy-after-enroll",
                    snapshot,
                    experiment,
                    provider=provider,
                    budget_id="legacy",
                    synthetic_fixture=True,
                )
            self.assertEqual(provider.calls, [])
            # Deliberately remove the shared policy guard. Observe actual legacy
            # provider requests, never a fake DSPy compiler or a status label.
            with patch("refinery.heldout_policy.check", return_value=None):
                evaluator.run(
                    store,
                    storage.VERSION,
                    "legacy-after-enroll",
                    snapshot,
                    experiment,
                    provider=provider,
                    budget_id="legacy",
                    synthetic_fixture=True,
                )
            with self.assertRaises(AssertionError):
                self.assertEqual(
                    provider.calls, [], "enrolled cohort must have no legacy calls"
                )

    def test_callback_cursor_and_claim_mutation_cannot_commit_owner(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)

            def rogue() -> None:
                session.provider.complete(
                    ev.Request(
                        "optimization",
                        "rogue",
                        "1",
                        "a" * 64,
                        session.config,
                        session.split.sha256,
                        1,
                    )
                )

            session.guards["optimization"] = rogue
            with self.assertRaisesRegex(ev.Halt, "lifecycle_provider_changed"):
                session.claim_compile()
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 0
            )
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0], 0
            )

    def test_rehashed_compiled_usage_and_candidate_tampering_is_rejected(self) -> None:
        for mutation in ("calls", "tokens", "candidate", "clock", "receipt"):
            with self.subTest(mutation=mutation):
                self.path += ".next"
                with storage.Store(self.path) as store:
                    session = prepare(store)
                    original = compile_fixture(session)
                    record = store.get("dspy_compiled", session.key)
                    if mutation in {"calls", "tokens"}:
                        usage = dict(original.usage)
                        usage[mutation] = 0
                        record["result"] = replace(
                            original, usage=tuple(sorted(usage.items()))
                        )
                    elif mutation == "candidate":
                        other = session.train[1]
                        candidate = opt.encode_candidate(
                            ((opt.case(other), other.gold.decode()),),
                            session.train,
                            session.split,
                            session.config,
                        )
                        record["result"] = replace(
                            original,
                            candidate=candidate,
                            candidate_sha256=digest(candidate),
                        )
                    elif mutation == "clock":
                        record["clock_ms"] = 0
                    else:
                        store.db.execute("UPDATE attempts SET outcome='unknown'")
                    if mutation != "receipt":
                        # Deliberately tamper with this disposable test DB, then
                        # restore its exact trigger and recompute object hashes.
                        # Tests target association checks beyond hash equality.
                        trigger = store.db.execute(
                            "SELECT sql FROM sqlite_master WHERE name='objects_UPDATE'"
                        ).fetchone()[0]
                        store.db.execute("DROP TRIGGER objects_UPDATE")
                        raw = storage.pack(record)
                        store.db.execute(
                            "UPDATE objects SET payload=?,digest=? WHERE kind='dspy_compiled'",
                            (raw, digest(raw)),
                        )
                        store.db.execute(trigger)
                        store.db.execute(
                            "UPDATE experiments SET report=?", (digest(raw),)
                        )
                    fresh = prepare(store)
                    with self.assertRaises(ValueError):
                        fresh.recover_compiled()
                    self.assertEqual(fresh.provider.calls, [])

    def test_completed_cache_checks_current_authority_and_claim_state(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            compile_fixture(session)
            evaluate_fixture(session)
            fresh = prepare(store)

            def denied() -> None:
                raise UseDenied("revoked")

            fresh.guards["evaluation"] = denied
            with self.assertRaises(UseDenied):
                fresh.recover_evaluated()
            fresh.guards["evaluation"] = lambda: store.db.execute(
                "UPDATE experiments SET state='evaluating'"
            )
            with self.assertRaises((ValueError, ev.Halt)):
                fresh.recover_evaluated()
            self.assertEqual(fresh.provider.calls, [])

    def test_policy_is_rechecked_after_retained_authority_callback(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            guards: Any = importlib.import_module("refinery.feedback_use")
            authority = guards.RetainedFeedbackUse(None, None)
            guard = guards.use_guard(
                store,
                session.snapshot_sha256,
                purpose="evaluation",
                configuration_sha256="a" * 64,
                run_sha256="b" * 64,
                retained_use=authority,
                synthetic_fixture=False,
            )
            # Authority stub changes enrollment during the final authority check.
            # No optimizer is replaced or executed by this boundary test.
            with patch.object(
                guards.RetainedFeedbackUse,
                "verify",
                side_effect=lambda *_: session.claim_compile(),
            ):
                with self.assertRaises(UseDenied):
                    guard()
            self.assertGreater(
                store.db.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind='dspy_partition'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(session.provider.calls, [])

    def test_rehashed_seal_and_score_tampering_are_rejected(self) -> None:
        for mutation in ("seal", "missing-seal", "scores", "scores-and-receipts"):
            with self.subTest(mutation=mutation):
                self.path += ".next"
                with storage.Store(self.path) as store:
                    session = prepare(store)
                    compile_fixture(session)
                    evaluate_fixture(session)
                    action = "DELETE" if mutation == "missing-seal" else "UPDATE"
                    trigger = store.db.execute(
                        "SELECT sql FROM sqlite_master WHERE name=?",
                        ("objects_" + action,),
                    ).fetchone()[0]
                    store.db.execute("DROP TRIGGER objects_" + action)
                    if mutation == "missing-seal":
                        key = store.db.execute(
                            "SELECT identity FROM objects WHERE kind='dspy_holdout' LIMIT 1"
                        ).fetchone()[0]
                        store.db.execute(
                            "DELETE FROM objects WHERE kind='dspy_holdout' AND identity=?",
                            (key,),
                        )
                    elif mutation == "seal":
                        rows = store.db.execute(
                            "SELECT identity,payload FROM objects WHERE kind='dspy_holdout'"
                        ).fetchall()
                        for key, raw in rows:
                            value = storage.unpack(raw)
                            value["candidate_sha256"] = "0" * 64
                            new = storage.pack(value)
                            store.db.execute(
                                "UPDATE objects SET payload=?,digest=? WHERE kind='dspy_holdout' AND identity=?",
                                (new, digest(new), key),
                            )
                    else:
                        raw = store.db.execute(
                            "SELECT payload FROM objects WHERE kind='dspy_evaluated'"
                        ).fetchone()[0]
                        value = storage.unpack(raw)
                        value["report"]["scores"] = {"baseline": 0, "candidate": 0}
                        if mutation == "scores-and-receipts":
                            report = value["report"]
                            wrong = digest(b"forged-answer")
                            report["predictions"] = tuple(
                                (*row[:3], wrong) for row in report["predictions"]
                            )
                            report["attempts"] = tuple(
                                replace(a, content_sha256=wrong)
                                for a in report["attempts"]
                            )
                            for ticket, raw_receipt in store.db.execute(
                                "SELECT ticket,receipt FROM attempts WHERE receipt IS NOT NULL"
                            ).fetchall():
                                receipt = storage.unpack(raw_receipt)
                                if receipt.request.stage != "optimization":
                                    store.db.execute(
                                        "UPDATE attempts SET receipt=? WHERE ticket=?",
                                        (
                                            storage.pack(
                                                replace(receipt, content_sha256=wrong)
                                            ),
                                            ticket,
                                        ),
                                    )
                        new = storage.pack(value)
                        store.db.execute(
                            "UPDATE objects SET payload=?,digest=? WHERE kind='dspy_evaluated'",
                            (new, digest(new)),
                        )
                        store.db.execute(
                            "UPDATE experiments SET report=?", (digest(new),)
                        )
                    store.db.execute(trigger)
                    fresh = prepare(store)
                    with self.assertRaises((ValueError, ev.Halt)):
                        fresh.recover_evaluated()
                    self.assertEqual(fresh.provider.calls, [])

    def test_postcommit_revocation_or_cancel_denies_outward_success(self) -> None:
        for phase in ("compile", "evaluation"):
            for event in ("revoke", "cancel"):
                with self.subTest(phase=phase, event=event):
                    self.path += ".next"
                    with storage.Store(self.path) as store:
                        session = prepare(store)

                        def after(point: str) -> None:
                            if point != "after_dspy_" + phase + "_commit":
                                return
                            if event == "cancel":
                                session.budget().cancel()
                            else:

                                def denied() -> None:
                                    raise UseDenied("revoked")

                                session.guards[
                                    "optimization"
                                    if phase == "compile"
                                    else "evaluation"
                                ] = denied

                        session.checkpoint = after
                        if phase == "compile":
                            with self.assertRaises(ev.Halt):
                                compile_fixture(session)
                        else:
                            compile_fixture(session)
                            with self.assertRaises(ev.Halt):
                                evaluate_fixture(session)
                        self.assertEqual(
                            store.db.execute(
                                "SELECT state FROM experiments"
                            ).fetchone()[0],
                            "compiled" if phase == "compile" else "complete",
                        )

    def test_invalid_cursor_container_is_not_iterated(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)

            class ForeignCursor:
                def __iter__(self) -> Any:
                    raise AssertionError("foreign cursor executed")

            session.provider.calls = ForeignCursor()
            with self.assertRaisesRegex(ev.Halt, "lifecycle_provider_changed"):
                session.guard("optimization")

    def test_changed_seal_stops_dispatch_before_reservation(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            compile_fixture(session)
            session.claim_evaluation()
            trigger = store.db.execute(
                "SELECT sql FROM sqlite_master WHERE name='objects_UPDATE'"
            ).fetchone()[0]
            store.db.execute("DROP TRIGGER objects_UPDATE")
            for key, raw in store.db.execute(
                "SELECT identity,payload FROM objects WHERE kind='dspy_holdout'"
            ).fetchall():
                value = storage.unpack(raw)
                value["candidate_sha256"] = "0" * 64
                changed = storage.pack(value)
                store.db.execute(
                    "UPDATE objects SET payload=?,digest=? WHERE kind='dspy_holdout' AND identity=?",
                    (changed, digest(changed), key),
                )
            store.db.execute(trigger)
            _, heldout = validate_dataset(session.dataset, session.split)
            dispatch = opt.Dispatch(
                heldout,
                session.split,
                stage="baseline",
                artifact_sha256="a" * 64,
                provider=session.provider,
                budget=session.budget(),
                per_call=session.per_call,
                check_use=lambda: session.guard("evaluation"),
            )
            before = len(session.provider.calls)
            with self.assertRaisesRegex(ev.Halt, "heldout_seal_changed"):
                dispatch.invoke(lambda **_: dispatch.forward([]), opt.case(heldout[0]))
            self.assertEqual(len(session.provider.calls), before)
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1
            )

    def test_callback_foreign_cursor_is_not_iterated(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)

            class ForeignCursor:
                def __iter__(self) -> Any:
                    raise AssertionError("foreign cursor executed")

            session.guards["optimization"] = lambda: setattr(
                session.provider, "calls", ForeignCursor()
            )
            with self.assertRaisesRegex(ev.Halt, "lifecycle_provider_changed"):
                session.guard("optimization")

    def test_missing_framework_has_no_claim_budget_or_enrollment(self) -> None:
        with storage.Store(self.path) as store:
            session = prepare(store)
            with patch(
                "refinery.dspy_optimizer.framework",
                side_effect=ModuleNotFoundError("dspy"),
            ):
                with self.assertRaises(ModuleNotFoundError):
                    session.compile()
            for table in ("experiments", "budgets", "attempts"):
                self.assertEqual(
                    store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
                )
            self.assertEqual(
                store.db.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind='dspy_partition'"
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
