"""Alternating real worker/main durable fixtures, not actual GEPA qualification."""

from __future__ import annotations

import importlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery import gepa_codec as codec
from refinery import gepa_optimizer as gepa
from refinery.dspy_optimizer import case
from refinery.gepa_evidence import GEPACompiled
from refinery.lineage import digest

legacy: Any = importlib.import_module("test_durable_gepa")
storage: Any = importlib.import_module("refinery.storage")


class DurableWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f = legacy.DurableGEPATests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def dispatch(self, session: Any) -> Any:
        return gepa.Transport(
            self.f.example,
            session.split,
            config=session.config,
            provider=session.provider,
            budget=session.budget(),
            per_call=session.per_call,
            check_use=lambda: session.guard("optimization"),
        )

    def worker_call(self, dispatch: Any, function: Any) -> Any:
        def work() -> Any:
            with dispatch.execution_scope():
                return function()

        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(work).result(timeout=20)

    def task(self, dispatch: Any, instruction: str) -> Any:
        return self.worker_call(
            dispatch,
            lambda: dispatch.invoke_sealed(
                lambda **_: dispatch.forward([]),
                "optimization",
                instruction,
                case(self.f.example),
            ),
        )

    def compile_fixture(self, session: Any) -> Any:
        # Main claims; original transport runs tasks/metrics on real workers;
        # main proposes and commits an explicitly constructed component result.
        session.claim_compile()
        d = self.dispatch(session)
        self.task(d, codec.INITIAL)
        self.assertEqual(
            self.worker_call(
                d,
                lambda: d.metric_feedback(
                    case(self.f.example), self.f.example.gold.decode(), "wrong", None
                ),
            ),
            (0.0, "incorrect"),
        )

        def reflection(**_: Any) -> Any:
            output = d.forward([]).choices[0].message.content
            return SimpleNamespace(instruction=output.split("\n")[1])

        with d.execution_scope():
            d.propose(
                reflection,
                candidate={"predict": codec.INITIAL},
                components_to_update=["predict"],
                reflective_dataset={
                    "predict": [
                        {
                            "Inputs": {"case": case(self.f.example)},
                            "Generated Outputs": {"answer": "wrong"},
                            "Feedback": "incorrect",
                        }
                    ]
                },
            )
        instruction, payload, attempt = d.proposals[0]
        self.task(d, instruction)
        self.assertEqual(
            self.worker_call(
                d,
                lambda: d.metric_feedback(
                    case(self.f.example),
                    self.f.example.gold.decode(),
                    self.f.example.gold.decode(),
                    None,
                ),
            ),
            (1.0, "correct"),
        )
        candidate = codec.encode(
            instruction,
            payload,
            attempt,
            session.dataset,
            session.split,
            session.config,
            tuple(d.attempts),
        )
        result = GEPACompiled(
            "compiled",
            None,
            session.config,
            candidate,
            digest(candidate),
            tuple(d.attempts),
            tuple(sorted(d.budget.snapshot().items())),
        )
        session.commit_compiled(result)
        return result

    def test_worker_authority_reads_witnesses_commit_and_zero_effect_replay(
        self,
    ) -> None:
        owner = threading.get_ident()
        observed: list[tuple[str, bool]] = []

        def authority(purpose: str) -> Any:
            def check() -> None:
                self.f.store.get("lineage", self.f.snapshot)
                self.f.store.db.execute("SELECT state FROM experiments").fetchall()
                observed.append((purpose, threading.get_ident() != owner))

            return check

        s = self.f.make(
            authority={p: authority(p) for p in ("optimization", "reflection")}
        )
        result = self.compile_fixture(s)
        for purpose in ("optimization", "reflection"):
            self.assertIn((purpose, True), observed)
            self.assertIn((purpose, False), observed)
        self.assertEqual(
            [r.stage for r in s.provider.calls],
            ["optimization", "reflection", "optimization"],
        )
        self.assertEqual(
            self.f.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='gepa_request_witness'"
            ).fetchone()[0],
            3,
        )
        attempts = tuple(self.f.store.db.execute("SELECT * FROM attempts"))
        ledger = tuple(self.f.store.db.execute("SELECT * FROM budgets"))
        with (
            patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime")
            ),
            patch(
                "refinery.dspy_optimizer.framework",
                side_effect=AssertionError("framework"),
            ),
        ):
            resumed = self.f.make()
            self.assertEqual(resumed.compile(), result)
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(
            tuple(self.f.store.db.execute("SELECT * FROM attempts")), attempts
        )
        self.assertEqual(
            tuple(self.f.store.db.execute("SELECT * FROM budgets")), ledger
        )

    def test_worker_checkpoint_cannot_replace_revoked_authority(self) -> None:
        state = {"revoked": False}

        def reflection() -> None:
            self.f.store.db.execute("SELECT 1").fetchone()
            if state["revoked"]:
                raise ev.Halt("reflection_revoked")

        def checkpoint(stage: str) -> None:
            if stage == "after_budget_reservation":
                state["revoked"] = True
                s.guards["reflection"] = lambda: None
                s._gepa_authority_routes = tuple(s.guards.items())

        s = self.f.make(authority={"reflection": reflection}, checkpoint=checkpoint)
        s.claim_compile()
        d = self.dispatch(s)
        with self.assertRaisesRegex(ev.Halt, "authority_changed"):
            self.task(d, codec.INITIAL)
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 1)
        self.assertEqual(
            self.f.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='gepa_request_witness'"
            ).fetchone()[0],
            1,
        )
        with self.assertRaisesRegex(ev.Halt, "interrupted_or_owned"):
            self.f.make().compile()

    def test_witness_failure_rolls_back_worker_reservation(self) -> None:
        s = self.f.session
        s.claim_compile()
        d = self.dispatch(s)
        before = tuple(self.f.store.db.execute("SELECT * FROM budgets"))
        original = storage.Store.put

        def fail(tx: Any, kind: str, identity: str, value: Any) -> Any:
            if kind == "gepa_request_witness":
                raise ValueError("witness write failed")
            return original(tx, kind, identity, value)

        with patch.object(storage.Store, "put", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "witness write failed"):
                self.task(d, codec.INITIAL)
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(
            tuple(self.f.store.db.execute("SELECT * FROM budgets")), before
        )
        self.assertEqual(
            self.f.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0
        )

    def test_unknown_worker_effect_checkpoint_replays_without_refund(self) -> None:
        self.f.script = ()
        s = self.f.make()
        s.claim_compile()
        d = self.dispatch(s)
        with self.assertRaisesRegex(ev.Halt, "provider_unknown"):
            self.task(d, codec.INITIAL)
        result = GEPACompiled(
            "blocked",
            "provider_unknown",
            s.config,
            None,
            None,
            tuple(d.attempts),
            tuple(sorted(d.budget.snapshot().items())),
        )
        s.commit_compiled(result)
        before = tuple(self.f.store.db.execute("SELECT * FROM budgets"))
        resumed = self.f.make()
        with patch.object(
            gepa, "runtime_identity", side_effect=AssertionError("runtime")
        ):
            self.assertEqual(resumed.compile(), result)
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(
            tuple(self.f.store.db.execute("SELECT * FROM budgets")), before
        )
        self.assertEqual(resumed.budget().snapshot()["unresolved_calls"], 1)

    def test_worker_heldout_evaluation_remains_unavailable(self) -> None:
        s = self.f.session
        with patch(
            "refinery.dspy_optimizer.framework", side_effect=AssertionError("framework")
        ):
            with self.assertRaisesRegex(ValueError, "not implemented"):
                self.worker_call(self.dispatch(s), s.evaluate)
        self.assertEqual(s.provider.calls, [])
