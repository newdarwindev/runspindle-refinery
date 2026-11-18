"""Synthetic admission/codec tests, not evidence that GEPA executed."""

from __future__ import annotations

import importlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from refinery import evaluation as ev
from refinery import gepa_codec as codec
from refinery import gepa_optimizer as gepa
from refinery.dspy_optimizer import case
from refinery.durable_evaluation import SQLiteBudget
from refinery.lineage import digest

storage: Any = importlib.import_module("refinery.storage")
fixture: Any = importlib.import_module("test_durable").setup


class GEPABoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = storage.Store(Path(temp.name) / "store.sqlite")
        self.addCleanup(self.store.close)
        snapshot, _, _ = fixture(self.store)
        self.dataset, self.split = self.store.lineage(snapshot)
        self.train = next(
            e
            for e in self.dataset.examples
            if self.split.partition(e.source.family_id) == "train"
        )
        self.heldout = next(
            e
            for e in self.dataset.examples
            if self.split.partition(e.source.family_id) == "heldout"
        )
        self.clock: Any = ev.VirtualClock()
        self.provider: Any = ev.ScriptedProvider(
            (
                ev.Completion(b"wrong", 1, 1, True, 0),
                ev.Completion(b"Read carefully and answer exactly.", 1, 1, True, 0),
                ev.Completion(self.train.gold, 1, 1, True, 0),
            ),
            clock=self.clock,
        )
        self.budget: Any = SQLiteBudget(
            self.store,
            "gepa",
            ev.Limits(16, 100, 100, 100),
            clock_ms=self.clock,
            experiment="gepa",
        )
        self.dispatch = gepa.Transport(
            self.train,
            self.split,
            config="a" * 64,
            provider=self.provider,
            budget=self.budget,
            per_call=ev.Reservation(10, 10),
            check_use=lambda: None,
        )

    def task(self) -> None:
        self.dispatch.invoke_sealed(
            lambda **_: self.dispatch.forward([]),
            "optimization",
            codec.INITIAL,
            case(self.train),
        )

    def propose(self, **changes: Any) -> dict[str, str]:
        args = dict(
            candidate={"predict": codec.INITIAL},
            components_to_update=["predict"],
            reflective_dataset={
                "predict": [
                    {
                        "Inputs": {"case": case(self.train)},
                        "Generated Outputs": {"answer": "wrong"},
                        "Feedback": "incorrect",
                    }
                ]
            },
        )
        args.update(changes)

        def predict(**_: Any) -> Any:
            raw = self.dispatch.forward([]).choices[0].message.content
            self.assertTrue(raw.startswith("[[ ## instruction ## ]]\n"))
            self.assertNotIn("[[ ## answer ## ]]", raw)
            return SimpleNamespace(instruction=raw.split("\n")[1])

        return self.dispatch.propose(predict, **args)

    def test_shared_cursor_and_domain_association_roundtrip(self) -> None:
        self.task()
        self.propose()
        instruction, payload, attempt = self.dispatch.proposals[0]
        calls = self.provider.calls
        self.assertEqual([r.stage for r in calls], ["optimization", "reflection"])
        self.assertEqual([r.attempt for r in calls], [1, 2])
        self.assertEqual(
            calls[1].artifact_sha256,
            codec.association("a" * 64, "reflection", codec.INITIAL, payload),
        )
        self.assertEqual(tuple(calls), self.dispatch.expected_calls)
        self.assertEqual(self.budget.snapshot()["calls"], 2)
        self.dispatch.invoke_sealed(
            lambda **_: self.dispatch.forward([]),
            "optimization",
            instruction,
            case(self.train),
        )
        attempts = tuple(self.dispatch.attempts)
        candidate = codec.encode(
            instruction, payload, attempt, self.dataset, self.split, "a" * 64, attempts
        )
        self.assertEqual(
            codec.decode(
                candidate,
                digest(candidate),
                self.dataset,
                self.split,
                "a" * 64,
                attempts,
            ),
            instruction,
        )
        for bad_config, bad_attempts in (
            ("b" * 64, attempts),
            ("a" * 64, attempts[:1]),
            ("a" * 64, (replace(attempts[0], content_sha256="b" * 64), attempts[1])),
        ):
            with self.assertRaises(ValueError):
                codec.decode(
                    candidate,
                    digest(candidate),
                    self.dataset,
                    self.split,
                    bad_config,
                    bad_attempts,
                )
        with self.assertRaises(ValueError):
            codec.decode(
                candidate + b" ",
                digest(candidate),
                self.dataset,
                self.split,
                "a" * 64,
                attempts,
            )
        with self.assertRaises(ValueError):
            self.propose()
        self.assertEqual(len(calls), 3)

    def test_unobserved_or_heldout_trace_never_dispatches(self) -> None:
        with self.assertRaises(ValueError):
            self.propose()
        self.task()
        for row in (
            {
                "Inputs": {"case": case(self.heldout)},
                "Generated Outputs": {"answer": "wrong"},
                "Feedback": "incorrect",
            },
            {
                "Inputs": {"case": case(self.train)},
                "Generated Outputs": {"answer": "invented"},
                "Feedback": "incorrect",
            },
            {
                "Inputs": {"case": case(self.train)},
                "Generated Outputs": {"answer": "wrong"},
                "Feedback": "private error",
            },
        ):
            with self.assertRaises(ValueError):
                self.propose(reflective_dataset={"predict": [row]})
        self.assertEqual(len(self.provider.calls), 1)
        with self.assertRaises(ValueError):
            self.dispatch.invoke_sealed(
                lambda **_: self.fail("called"),
                "optimization",
                codec.INITIAL,
                case(self.heldout),
            )

    def test_unattributed_and_nested_calls_reject(self) -> None:
        with self.assertRaisesRegex(ev.Halt, "unattributed_dispatch"):
            self.dispatch.forward([])
        self.assertEqual(self.provider.calls, [])

    def test_unknown_usage_retains_reservation_no_proposal(self) -> None:
        self.provider.script = (ev.Completion(b"wrong", None, 1, True, 0),)
        self.dispatch.script_sha256 = self.provider.sha256
        with self.assertRaisesRegex(ev.Halt, "unknown_or_invalid_usage"):
            self.task()
        self.assertEqual(self.budget.snapshot()["unresolved_calls"], 1)
        self.assertEqual(self.dispatch.trace, [])
        self.assertEqual(self.dispatch.proposals, [])
        with self.assertRaises(ev.Halt):
            self.task()
        self.assertEqual(len(self.provider.calls), 1)

    def test_foreign_cursor_cancellation_and_bounds_fail_closed(self) -> None:
        self.provider.calls.append(
            ev.Request(
                "optimization", "foreign", "1", "b" * 64, "c" * 64, self.split.sha256, 1
            )
        )
        with self.assertRaisesRegex(ev.Halt, "dispatch_binding_changed"):
            self.task()
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_cancel_before_reflection_does_not_call(self) -> None:
        self.task()
        self.budget.stop("cancelled")
        before = self.budget.snapshot()
        with self.assertRaisesRegex(ev.Halt, "cancelled"):
            self.propose()
        self.assertEqual(self.budget.snapshot(), before)
        self.assertEqual(len(self.provider.calls), 1)

    def test_metric_requires_latest_task_receipt_not_historical_answer(self) -> None:
        self.task()
        self.assertEqual(
            self.dispatch.metric_feedback(
                case(self.train), self.train.gold.decode(), "wrong", None
            ),
            (0.0, "incorrect"),
        )
        self.propose()
        with self.assertRaisesRegex(ValueError, "latest task receipt"):
            self.dispatch.metric_feedback(
                case(self.train), self.train.gold.decode(), "wrong", None
            )
        instruction = self.dispatch.proposals[0][0]
        self.dispatch.invoke_sealed(
            lambda **_: self.dispatch.forward([]),
            "optimization",
            instruction,
            case(self.train),
        )
        with self.assertRaisesRegex(ValueError, "latest task receipt"):
            self.dispatch.metric_feedback(
                case(self.train), self.train.gold.decode(), "wrong", None
            )
        self.assertEqual(
            self.dispatch.metric_feedback(
                case(self.train),
                self.train.gold.decode(),
                self.train.gold.decode(),
                "predict",
            ),
            (1.0, "correct"),
        )

    def test_unknown_adapters_rejected_without_reading_properties(self) -> None:
        touched: list[str] = []

        class Unknown:
            @property
            def calls(self) -> Any:
                touched.append("calls")
                raise AssertionError("unknown calls read")

            @property
            def limits(self) -> Any:
                touched.append("limits")
                raise AssertionError("unknown limits read")

            @property
            def clock_ms(self) -> Any:
                touched.append("clock")
                raise AssertionError("unknown clock read")

        for field in ("provider", "budget", "per_call"):
            arguments: Any = dict(
                provider=self.provider,
                budget=self.budget,
                per_call=ev.Reservation(10, 10),
                check_use=lambda: None,
            )
            arguments[field] = Unknown()
            with self.assertRaisesRegex(ValueError, "explicit offline adapters"):
                gepa.optimize(
                    self.dataset,
                    self.split,
                    training_output_id=self.train.output.output_id,
                    **arguments,
                )
        self.assertEqual(touched, [])
        self.assertEqual(self.provider.calls, [])

    def test_successful_trace_cannot_enter_reflection_directly(self) -> None:
        self.provider.script = (ev.Completion(self.train.gold, 1, 1, True, 0),)
        self.dispatch.script_sha256 = self.provider.sha256
        self.task()
        payload = codec.proposal_payload(
            "a" * 64,
            case(self.train),
            self.train.gold.decode(),
            "correct",
            self.dispatch.attempts[-1],
        )
        with self.assertRaisesRegex(ValueError, "unverified proposal payload"):
            self.dispatch.invoke_sealed(
                lambda **_: self.fail("reflection dispatched"),
                "reflection",
                codec.INITIAL,
                payload,
            )
        self.assertEqual(len(self.provider.calls), 1)

    def test_complete_history_bound_in_locked_candidate(self) -> None:
        self.task()
        self.propose()
        instruction, payload, reflection = self.dispatch.proposals[0]
        self.dispatch.invoke_sealed(
            lambda **_: self.dispatch.forward([]),
            "optimization",
            instruction,
            case(self.train),
        )
        attempts = tuple(self.dispatch.attempts)
        candidate = codec.encode(
            instruction,
            payload,
            reflection,
            self.dataset,
            self.split,
            "a" * 64,
            attempts,
        )
        for field in ("tokens", "cost_microunits", "latency_ms"):
            fields: dict[str, Any] = {field: 2}
            changed = attempts[:-1] + (replace(attempts[-1], **fields),)
            with self.assertRaisesRegex(ValueError, "instruction association changed"):
                codec.decode(
                    candidate,
                    digest(candidate),
                    self.dataset,
                    self.split,
                    "a" * 64,
                    changed,
                )
        extra = replace(attempts[-1], request=replace(attempts[-1].request, attempt=4))
        with self.assertRaisesRegex(ValueError, "instruction association changed"):
            codec.decode(
                candidate,
                digest(candidate),
                self.dataset,
                self.split,
                "a" * 64,
                attempts + (extra,),
            )

    def test_changed_association_preserves_first_terminal_reason(self) -> None:
        self.budget.stop("cancelled")
        before = self.budget.snapshot()
        self.dispatch.config = "b" * 64
        calls: list[str] = []

        def must_not_run() -> None:
            calls.append("callback")
            raise AssertionError("callback invoked")

        self.dispatch.check_use = must_not_run
        with self.assertRaisesRegex(ev.Halt, "^cancelled$"):
            self.dispatch.guard()
        self.assertEqual(calls, [])
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot(), before)

    def test_admission_callback_cannot_change_association(self) -> None:
        def change() -> None:
            self.dispatch.config = "b" * 64

        self.dispatch.check_use = change
        with self.assertRaisesRegex(ev.Halt, "gepa_binding_changed"):
            self.task()
        self.assertEqual(self.provider.calls, [])

    def test_unqualified_runtime_has_zero_effects(self) -> None:
        with self.assertRaisesRegex(ValueError, "unqualified GEPA Python or target"):
            gepa.optimize(
                self.dataset,
                self.split,
                training_output_id=self.train.output.output_id,
                provider=self.provider,
                budget=self.budget,
                per_call=ev.Reservation(10, 10),
                check_use=lambda: None,
            )
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.budget.snapshot()["calls"], 0)

    def test_payload_and_instruction_limits(self) -> None:
        for value in ("", "x" * 4097, True):
            with self.assertRaises(ValueError):
                codec.text(value)
        with self.assertRaises(ValueError):
            codec.association("a" * 64, "reflection", codec.INITIAL, "x" * 65537)

    def test_extra_dispatch_or_lost_trace_breaks_codec(self) -> None:
        self.task()
        self.propose()
        instruction, payload, attempt = self.dispatch.proposals[0]
        for attempts in (
            tuple(self.dispatch.attempts[1:]),
            (
                self.dispatch.attempts[0],
                replace(
                    attempt, request=replace(attempt.request, artifact_sha256="c" * 64)
                ),
            ),
        ):
            with self.assertRaises(ValueError):
                codec.encode(
                    instruction,
                    payload,
                    attempt,
                    self.dataset,
                    self.split,
                    "a" * 64,
                    attempts,
                )


if __name__ == "__main__":
    unittest.main()
