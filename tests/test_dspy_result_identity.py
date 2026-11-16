"""Immutable lifecycle/result boundaries using real SQLite and handwritten fixtures.

No DSPy import or framework execution; provider requests are existing synthetic
transport fixtures. Negative checks inspect durable state rather than status alone.
"""

import importlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import dspy_optimizer as opt
from refinery.durable_dspy import Session
from refinery.lineage import digest

fixture = importlib.import_module("test_durable_dspy")
ROOT = Path(__file__).resolve().parents[1]


class ResultIdentityTests(unittest.TestCase):
    setUp: Any = fixture.DurableDSPyTests.setUp
    path: str

    def instrument(self, session: Session, phase: str) -> dict[str, Any]:
        """Hold the caller's result exactly as an ordinary checkpoint closure can."""
        capture: dict[str, Any] = {}
        original = getattr(session, "commit_" + phase)

        def commit(value: Any) -> None:
            capture["result"] = value
            capture["bytes"] = fixture.storage.pack(value)
            capture["calls"] = tuple(session.provider.calls)
            original(value)

        setattr(session, "commit_" + phase, commit)
        return capture

    def mutate(self, session: Session, capture: dict[str, Any], phase: str) -> None:
        if phase == "compiled":
            # Change the caller's bytes without changing the immutable record.
            candidate = b"synthetic-mutated-candidate"
            object.__setattr__(capture["result"], "candidate", candidate)
            object.__setattr__(capture["result"], "candidate_sha256", digest(candidate))
        else:
            capture["result"]["scores"]["baseline"] = 0

    def test_precommit_compiled_mutation_rolls_back_exact_checkpoint(self) -> None:
        with fixture.storage.Store(self.path) as store:
            session = fixture.prepare(store)
            capture = self.instrument(session, "compiled")

            def checkpoint(point: str) -> None:
                if point == "before_dspy_compile_commit":
                    self.mutate(session, capture, "compiled")

            session.checkpoint = checkpoint
            with self.assertRaisesRegex(ValueError, "result identity changed"):
                fixture.compile_fixture(session)
            self.assertEqual(session.row(store.db)[1:], ("compiling", None))
            self.assertEqual(
                store.db.execute(
                    "SELECT count(*) FROM objects WHERE kind='dspy_compiled'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(tuple(session.provider.calls), capture["calls"])
            # Settled synthetic effects/usage are never rolled back or replayed.
            self.assertEqual(session.budget().snapshot()["calls"], 1)
        self.child("compiled", None, "compiling")

    def test_precommit_evaluated_mutation_rolls_back_not_late_complete(self) -> None:
        with fixture.storage.Store(self.path) as store:
            session = fixture.prepare(store)
            fixture.compile_fixture(session)
            capture = self.instrument(session, "evaluated")

            def checkpoint(point: str) -> None:
                if point == "before_dspy_evaluation_commit":
                    self.mutate(session, capture, "evaluated")

            session.checkpoint = checkpoint
            with self.assertRaisesRegex(ValueError, "result identity changed"):
                fixture.evaluate_fixture(session)
            self.assertEqual(session.row(store.db)[1], "evaluating")
            self.assertEqual(
                store.db.execute(
                    "SELECT count(*) FROM objects WHERE kind='dspy_evaluated'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(tuple(session.provider.calls), capture["calls"])
        self.child("evaluated", None, "evaluating")

    def test_postcommit_mutations_deny_without_rewriting_history(self) -> None:
        for phase, point, state in (
            ("compiled", "after_dspy_compile_commit", "compiled"),
            ("evaluated", "after_dspy_evaluation_commit", "complete"),
        ):
            with self.subTest(phase=phase):
                self.path += ".next"
                with fixture.storage.Store(self.path) as store:
                    session = fixture.prepare(store)
                    if phase == "evaluated":
                        fixture.compile_fixture(session)
                    capture = self.instrument(session, phase)

                    def checkpoint(observed: str) -> None:
                        if observed == point:
                            self.mutate(session, capture, phase)

                    session.checkpoint = checkpoint
                    with self.assertRaises(ValueError):
                        (
                            fixture.compile_fixture(session)
                            if phase == "compiled"
                            else fixture.evaluate_fixture(session)
                        )
                    self.assertEqual(session.row(store.db)[1], state)
                    stored = store.get("dspy_" + phase, session.key)
                    field = "result" if phase == "compiled" else "report"
                    self.assertEqual(
                        fixture.storage.pack(stored[field]), capture["bytes"]
                    )
                    self.assertNotEqual(
                        fixture.storage.pack(capture["result"]), capture["bytes"]
                    )
                    self.assertEqual(tuple(session.provider.calls), capture["calls"])
                self.child(phase, capture["bytes"].hex(), state)

    def test_initial_authority_callback_cannot_replace_valid_result(self) -> None:
        with fixture.storage.Store(self.path) as store:
            session = fixture.prepare(store)
            session.claim_compile()
            budget = session.budget()
            budget.cancel()
            result = opt.Compiled(
                "blocked",
                "cancelled",
                session.config,
                None,
                None,
                (),
                tuple(sorted(budget.snapshot().items())),
            )
            original = session.guards["optimization"]

            def guard() -> None:
                original()
                # Both reasons are nonempty valid blocked-result values. No
                # candidate/parser check can detect this identity replacement.
                object.__setattr__(result, "reason", "different_block_reason")

            session.guards["optimization"] = guard
            with self.assertRaisesRegex(ValueError, "result identity changed"):
                session.commit_compiled(result)
            self.assertEqual(session.row(store.db)[1:], ("compiling", None))
            self.assertEqual(
                store.db.execute(
                    "SELECT count(*) FROM objects WHERE kind='dspy_compiled'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(session.provider.calls, [])

    def test_authority_callback_after_checkpoint_cannot_mutate_result(self) -> None:
        with fixture.storage.Store(self.path) as store:
            session = fixture.prepare(store)
            capture = self.instrument(session, "compiled")
            original = session.guards["optimization"]

            def checkpoint(point: str) -> None:
                if point == "before_dspy_compile_commit":

                    def guard() -> None:
                        original()
                        self.mutate(session, capture, "compiled")

                    session.guards["optimization"] = guard

            session.checkpoint = checkpoint
            with self.assertRaisesRegex(ValueError, "result identity changed"):
                fixture.compile_fixture(session)
            self.assertEqual(session.row(store.db)[1:], ("compiling", None))

    def test_removed_identity_guard_is_detected(self) -> None:
        # Deliberate removal of only the new invariant. The existing candidate
        # validators still see the original stored candidate, but this test
        # must independently detect the divergent caller result/commit.
        with patch.object(Session, "verify_result_identity", return_value=None):
            result = unittest.TestResult()
            ResultIdentityTests(
                "test_precommit_compiled_mutation_rolls_back_exact_checkpoint"
            ).run(result)
        self.assertEqual(result.errors, [], repr(result.errors))
        self.assertTrue(result.failures, "removed identity guard escaped regression")

    def child(self, phase: str, expected: str | None, state: str) -> None:
        script = """
import json, socket, sys
from unittest.mock import patch
from test_durable_dspy import prepare, storage
from refinery import evaluation as ev
with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')) as sockets:
 with storage.Store(sys.argv[1]) as store:
  session = prepare(store)
  with patch('refinery.dspy_optimizer.framework', side_effect=AssertionError('framework forbidden')):
   if sys.argv[3] == 'none':
    try:
     session.compile() if sys.argv[2] == 'compiled' else session.evaluate()
    except ev.Halt: pass
    else: raise RuntimeError('uncertain phase replayed')
   else:
    value = session.compile() if sys.argv[2] == 'compiled' else session.evaluate()
    if storage.pack(value).hex() != sys.argv[3]: raise RuntimeError('history changed')
  if session.provider.calls: raise RuntimeError('provider replay')
  if session.row(store.db)[1] != sys.argv[4]: raise RuntimeError('state changed')
  sockets.assert_not_called()
  print(json.dumps({'calls': 0, 'state': session.row(store.db)[1]}))
"""
        result = subprocess.run(
            [
                sys.executable,
                "-O",
                "-c",
                script,
                self.path,
                phase,
                expected or "none",
                state,
            ],
            cwd=ROOT,
            env=dict(
                os.environ, PYTHONPATH=str(ROOT) + os.pathsep + str(ROOT / "tests")
            ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"calls": 0, "state": state})
