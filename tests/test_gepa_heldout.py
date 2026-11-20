"""Real owner/ledger/candidate boundaries; hand-built result fixtures, no DSPy."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from refinery import dspy_optimizer as opt
from refinery import evaluation as ev
from refinery import gepa_codec as codec
from refinery import gepa_optimizer as gepa
from refinery.lineage import digest
from refinery.optimization import validate_dataset
from refinery.storage import pack


class HeldoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module("test_durable_gepa").DurableGEPATests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.heldout = validate_dataset(self.h.session.dataset, self.h.session.split)[1]
        self.h.script += tuple(
            ev.Completion(
                e.gold if correct else b"fixture wrong", tokens, cost, True, 0
            )
            for correct, tokens, cost in ((False, 3, 7), (True, 2, 4))
            for e in self.heldout
        )
        self.s = self.h.make()
        self.h.session = self.s
        self.compiled = self.h.compile_fixture()

    def evaluate_fixture(self) -> dict[str, Any]:
        s = self.s
        compiled = s.claim_evaluation()
        attempts = []
        predictions = []
        scores = {"baseline": 0, "candidate": 0}
        for stage in ("baseline", "candidate"):
            d = opt.Dispatch(
                tuple(self.heldout),
                s.split,
                stage=stage,
                artifact_sha256=codec.baseline_artifact()
                if stage == "baseline"
                else compiled.candidate_sha256,
                provider=s.provider,
                budget=s.budget(),
                per_call=s.per_call,
                check_use=lambda: s.guard("evaluation"),
            )
            for e in self.heldout:
                d.invoke(lambda **_: d.forward([]), opt.case(e))
                answer = d.attempts[-1].content_sha256
                if answer is None:
                    self.fail("missing synthetic prediction digest")
                predictions.append(
                    (stage, e.output.output_id, e.output.revision, answer)
                )
                scores[stage] += int(answer == digest(e.gold))
            attempts.extend(d.attempts)
        result = dict(
            status="evaluated",
            scores=scores,
            attempts=tuple(attempts),
            predictions=tuple(predictions),
            candidate_sha256=compiled.candidate_sha256,
            usage=s.budget().snapshot(),
            quality="synthetic_exact_match_only",
            promotion="not_authorized",
        )
        s.commit_evaluated(result)
        return result

    def test_one_owner_seal_ledger_and_no_import_replay(self) -> None:
        witnesses = tuple(
            self.s.store.db.execute(
                "SELECT * FROM objects WHERE kind='gepa_request_witness'"
            )
        )
        result = self.evaluate_fixture()
        self.assertEqual(self.s.compiled()[0], self.compiled)
        self.assertEqual(
            tuple(
                self.s.store.db.execute(
                    "SELECT * FROM objects WHERE kind='gepa_request_witness'"
                )
            ),
            witnesses,
        )
        self.assertEqual(self.s.budget().snapshot()["calls"], 3 + 2 * len(self.heldout))
        self.assertEqual(
            self.s.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='gepa_evaluated'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.s.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='dspy_evaluated'"
            ).fetchone()[0],
            0,
        )
        resumed = self.h.make()
        with (
            patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime")
            ),
            patch.object(opt, "framework", side_effect=AssertionError("framework")),
        ):
            self.assertEqual(resumed.evaluate(), result)
            self.assertEqual(resumed.evaluate(), result)
        self.assertEqual(resumed.provider.calls, [])
        with self.assertRaises(ev.Halt):
            resumed.guard("optimization")

    def test_measured_heldout_reuses_exact_durable_receipts_without_dispatch(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "completed GEPA heldout measurement"):
            self.s.measured_heldout()
        report = self.evaluate_fixture()
        resumed = self.h.make()
        with (
            patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime")
            ),
            patch.object(opt, "framework", side_effect=AssertionError("framework")),
        ):
            measured = resumed.measured_heldout()
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(measured["version"], "runspindle.gepa-heldout-measurement.v1")
        self.assertEqual(measured["evidence_class"], "offline_scripted_provider")
        self.assertEqual(measured["quality"], "synthetic_exact_match_only")
        self.assertEqual(measured["promotion"], "not_authorized")
        self.assertEqual(measured["candidate_sha256"], report["candidate_sha256"])
        self.assertEqual(measured["baseline_sha256"], codec.baseline_artifact())
        self.assertEqual(measured["binding_sha256"], resumed.binding_sha256)
        self.assertEqual(measured["configuration_sha256"], resumed.config)
        self.assertEqual(measured["split_sha256"], resumed.split.sha256)
        stored_sha = resumed.store.db.execute(
            "SELECT report FROM experiments WHERE identity=?", (resumed.key,)
        ).fetchone()[0]
        self.assertEqual(measured["report_sha256"], stored_sha)
        cases = sorted(
            digest(pack((e.output.output_id, e.output.revision))) for e in self.heldout
        )
        self.assertEqual(
            measured["rows"],
            {
                stage: [
                    {
                        "case": case,
                        "correct": correct,
                        "cost_micros": cost,
                        "tokens": tokens,
                    }
                    for case in cases
                ]
                for stage, correct, cost, tokens in (
                    ("baseline", False, 7, 3),
                    ("candidate", True, 4, 2),
                )
            },
        )
        self.assertEqual(resumed.measured_heldout(), measured)
        script = [
            (reply.content.hex(), reply.tokens, reply.cost_microunits, reply.safety)
            for reply in self.h.script
        ]
        child = subprocess.run(
            [
                sys.executable,
                "-B",
                *(["-O"] if sys.flags.optimize else []),
                "-c",
                """
import json
import sys
from unittest.mock import patch
from refinery import dspy_optimizer, evaluation, gepa_optimizer
from refinery.durable_dspy import Session
from refinery.storage import Store, VERSION

script = tuple(evaluation.Completion(bytes.fromhex(body), tokens, cost, safety)
               for body, tokens, cost, safety in json.load(sys.stdin))
with Store(sys.argv[1]) as store:
    provider = evaluation.ScriptedProvider(script, clock=evaluation.VirtualClock())
    session = Session(store, VERSION, 'gepa-run', sys.argv[2], provider=provider,
                      limits=evaluation.Limits(16, 100, 100, 100),
                      per_call=evaluation.Reservation(10, 10), budget_id='shared',
                      strategy='gepa', training_output_id=sys.argv[3], synthetic_fixture=True)
    with patch.object(dspy_optimizer, 'framework', side_effect=RuntimeError('framework forbidden')), \
         patch.object(gepa_optimizer, 'runtime_identity', side_effect=RuntimeError('runtime forbidden')):
        print(json.dumps({'evidence': session.measured_heldout(), 'calls': provider.calls}))
""",
                str(resumed.store.path),
                self.h.snapshot,
                self.h.example.output.output_id,
            ],
            input=json.dumps(script),
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        self.assertEqual(json.loads(child.stdout), {"evidence": measured, "calls": []})

    def test_measurements_refuse_revoked_authority_and_missing_attempt_receipt(
        self,
    ) -> None:
        self.evaluate_fixture()

        def deny() -> None:
            raise ev.Halt("evaluation_revoked")

        denied = self.h.make(authority={"evaluation": deny})
        with self.assertRaisesRegex(ev.Halt, "evaluation_revoked"):
            denied.measured_heldout()
        self.assertEqual(denied.provider.calls, [])
        resumed = self.h.make()
        with resumed.store.transaction() as tx:
            tx.execute(
                "DELETE FROM attempts WHERE ticket=(SELECT max(ticket) FROM attempts WHERE experiment=?)",
                (resumed.key,),
            )
        with self.assertRaises(ValueError):
            resumed.measured_heldout()
        self.assertEqual(resumed.provider.calls, [])

    def test_evaluation_requires_current_independent_authority(self) -> None:
        def deny() -> None:
            raise ev.Halt("evaluation_revoked")

        denied = self.h.make(authority={"evaluation": deny})
        with self.assertRaisesRegex(ev.Halt, "evaluation_revoked"):
            denied.claim_evaluation()
        self.assertEqual(denied.provider.calls, [])
        self.assertEqual(denied.row(denied.store.db)[1], "compiled")

        # Training/reflection denial must not substitute for evaluation consent or
        # reenter training after the owner seals its heldout cohort.
        def no_train() -> None:
            raise ev.Halt("training_forbidden")

        allowed = self.h.make(
            authority={"optimization": no_train, "reflection": no_train}
        )
        allowed.claim_evaluation()
        allowed.guard("evaluation")
        self.assertEqual(
            allowed.provider.calls, [a.request for a in self.compiled.attempts]
        )

    def test_tampered_instruction_history_and_crosscodec_deny_before_runtime(
        self,
    ) -> None:
        baseline = self.s.budget().snapshot()
        variants = (
            replace(self.compiled, candidate=self.compiled.candidate + b" "),
            replace(self.compiled, candidate_sha256="f" * 64),
            replace(self.compiled, attempts=self.compiled.attempts[:-1]),
            opt.Compiled("compiled", None, self.s.config, b"{}", "a" * 64, (), ()),
        )
        for candidate in variants:
            with patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime")
            ) as runtime:
                with self.assertRaises(ValueError):
                    gepa.evaluate_locked(
                        candidate,
                        dataset=self.s.dataset,
                        split=self.s.split,
                        training_output_id=self.s.training_output_id,
                        provider=self.s.provider,
                        budget=self.s.budget(),
                        per_call=self.s.per_call,
                        check_use=lambda: None,
                    )
                runtime.assert_not_called()
        self.assertEqual(self.s.budget().snapshot(), baseline)

    def test_wrong_baseline_and_changed_report_are_rejected(self) -> None:
        result = self.evaluate_fixture()
        first = result["attempts"][0]
        legacy = digest(
            opt.canonical({"signature": opt.SIGNATURE, "demonstrations": []})
        )
        self.assertNotEqual(legacy, codec.baseline_artifact())
        altered = dict(
            result,
            attempts=(
                replace(first, request=replace(first.request, artifact_sha256=legacy)),
            )
            + result["attempts"][1:],
        )
        with self.assertRaises(ValueError):
            self.s.verify_evaluated(altered, self.compiled)
        with self.assertRaises(ValueError):
            self.s.verify_evaluated(
                dict(result, candidate_sha256="a" * 64), self.compiled
            )

    def test_unknown_heldout_reservation_remains_charged_and_stops_retries(
        self,
    ) -> None:
        # Use a fresh lifecycle with an immutable script that has no evaluation
        # completion; never change a script after configuration/claim binding.
        self.h.doCleanups()
        self.h.setUp()
        self.s = self.h.session
        self.compiled = self.h.compile_fixture()
        self.s.claim_evaluation()
        heldout = validate_dataset(self.s.dataset, self.s.split)[1]
        d = opt.Dispatch(
            tuple(heldout),
            self.s.split,
            stage="baseline",
            artifact_sha256=codec.baseline_artifact(),
            provider=self.s.provider,
            budget=self.s.budget(),
            per_call=self.s.per_call,
            check_use=lambda: self.s.guard("evaluation"),
        )
        with self.assertRaisesRegex(ev.Halt, "provider_unknown"):
            d.invoke(lambda **_: d.forward([]), opt.case(heldout[0]))
        result = dict(
            status="blocked",
            scores={},
            attempts=tuple(d.attempts),
            predictions=(),
            candidate_sha256=self.compiled.candidate_sha256,
            usage=d.budget.snapshot(),
        )
        self.s.commit_evaluated(result)
        self.assertEqual(self.s.budget().snapshot()["unresolved_calls"], 1)
        count = len(self.s.provider.calls)
        with self.assertRaises(ev.Halt):
            d.invoke(lambda **_: d.forward([]), opt.case(heldout[0]))
        self.assertEqual(len(self.s.provider.calls), count)
        self.assertEqual(self.h.make().evaluate(), result)

    def test_interrupted_evaluation_claim_does_not_recompile_or_refund(self) -> None:
        def interrupt(stage: str) -> None:
            if stage == "after_dspy_evaluation_claim":
                raise RuntimeError("interrupted")

        s = self.h.make(checkpoint=interrupt)
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            s.claim_evaluation()
        resumed = self.h.make()
        with patch.object(opt, "framework", side_effect=AssertionError("framework")):
            with self.assertRaisesRegex(ev.Halt, "interrupted_or_owned"):
                resumed.evaluate()
        self.assertEqual(resumed.budget().snapshot()["calls"], 3)
        self.assertEqual(resumed.provider.calls, [])

    def test_cancellation_prevents_evaluation_claim(self) -> None:
        self.s.budget().stop("cancelled")
        with self.assertRaisesRegex(ev.Halt, "cancelled"):
            self.s.claim_evaluation()
        self.assertEqual(self.s.row(self.s.store.db)[1], "compiled")
        self.assertEqual(len(self.s.provider.calls), 3)

    def test_route_swap_after_evaluation_reservation_denies_effect(self) -> None:
        def mutate(stage: str) -> None:
            if stage == "after_budget_reservation":
                s.guards["evaluation"] = lambda: None

        s = self.h.make(checkpoint=mutate)
        s.claim_evaluation()
        d = opt.Dispatch(
            tuple(self.heldout),
            s.split,
            stage="baseline",
            artifact_sha256=codec.baseline_artifact(),
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("evaluation"),
        )
        with self.assertRaisesRegex(ev.Halt, "authority_changed"):
            d.invoke(lambda **_: d.forward([]), opt.case(self.heldout[0]))
        self.assertEqual(len(s.provider.calls), len(self.compiled.attempts))
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 1)

    def test_compile_row_after_heldout_and_extra_witness_rejected(self) -> None:
        self.evaluate_fixture()
        storage = importlib.import_module("refinery.storage")
        request = replace(self.compiled.attempts[-1].request, attempt=4)
        self.s.store.db.execute(
            "INSERT INTO attempts VALUES(?,?,?,'unknown',NULL)",
            (self.s.key, 999, storage.pack(request)),
        )
        with self.assertRaisesRegex(ValueError, "compilation follows"):
            self.s.compiled()
        self.s.store.db.execute("DELETE FROM attempts WHERE ticket=999")
        with self.s.store.transaction() as tx:
            self.s.store.put(
                tx, "gepa_request_witness", self.s.key + ":999", ("foreign",)
            )
        with self.assertRaisesRegex(ValueError, "witness reservation set"):
            self.s.compiled()

    def test_exhaustion_at_first_heldout_reservation_keeps_compile_budget(self) -> None:
        self.h.doCleanups()
        self.h.setUp()
        from refinery.durable_dspy import Session

        storage = importlib.import_module("refinery.storage")
        original = self.h.session
        s = Session(
            self.h.store,
            storage.VERSION,
            "gepa-run",
            self.h.snapshot,
            provider=original.provider,
            limits=ev.Limits(3, 100, 100, 100),
            per_call=original.per_call,
            budget_id="shared",
            strategy="gepa",
            training_output_id=self.h.example.output.output_id,
            synthetic_fixture=True,
        )
        self.h.session = s
        compiled = self.h.compile_fixture()
        s.claim_evaluation()
        heldout = validate_dataset(s.dataset, s.split)[1]
        d = opt.Dispatch(
            tuple(heldout),
            s.split,
            stage="baseline",
            artifact_sha256=codec.baseline_artifact(),
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("evaluation"),
        )
        with self.assertRaisesRegex(ev.Halt, "calls_exhausted"):
            d.invoke(lambda **_: d.forward([]), opt.case(heldout[0]))
        self.assertEqual(s.budget().snapshot()["calls"], 3)
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 0)
        self.assertEqual(len(s.provider.calls), len(compiled.attempts))

    def test_fixed_program_rechecks_mutation_after_current_authority(self) -> None:
        class Signature:
            def __init__(self, value: str):
                self.value = value

            def equals(self, other: Any) -> bool:
                return bool(self.value == other.value)

        fixed = Signature("fixed")
        predict = SimpleNamespace(demos=[], signature=Signature("fixed"))
        student = SimpleNamespace(predict=predict, predictors=lambda: [predict])
        gepa.heldout_program_guard(student, predict, fixed, lambda: None)
        for mutation in (
            lambda: setattr(predict, "demos", ["injected"]),
            lambda: setattr(predict, "signature", Signature("changed")),
            lambda: setattr(student, "predict", object()),
        ):
            predict.demos = []
            predict.signature = Signature("fixed")
            student.predict = predict
            with self.assertRaisesRegex(ev.Halt, "program_changed"):
                gepa.heldout_program_guard(student, predict, fixed, mutation)
