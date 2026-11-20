"""Source/oracle tests only; no actual DSPy/GEPA execution claim."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery import gepa_runtime as runtime
from tools import offline_gepa as launch
from tools import test_gepa_heldout_adversarial as h
from tools import test_gepa_heldout_materialized as base


def phases(case: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    first, _, _ = importlib.import_module("test_gepa_heldout_harness").phases()
    from refinery.storage import pack

    compiled = []
    for i in range(5):
        compiled.append(
            ev.Attempt(
                ev.Request(
                    "reflection" if i == 2 else "optimization",
                    "train",
                    "v1",
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    i + 1,
                ),
                "completed",
                "d" * 64,
                1,
                1,
                True,
                0,
            )
        )
    first["compile_attempts"] = [asdict(a) for a in compiled]
    first["identity"]["attempts"] = runtime.digest(pack(tuple(compiled)))
    first["framework"]["attempts_sha256"] = runtime.digest(
        runtime.canonical(first["compile_attempts"])
    )
    expected = dict(
        stage="baseline",
        output_id="heldout",
        output_revision="v1",
        source_sha256="e" * 64,
        artifact_sha256="f" * 64,
        split_sha256="c" * 64,
        attempt=1,
        content_sha256="d" * 64,
    )
    first["expected_heldout"] = expected
    cancelled = case == "cancelled"
    unknown = case.startswith("unknown_")
    usage = dict(
        calls=5 if cancelled else 6,
        tokens=None if unknown else 5 if cancelled else 6,
        cost_microunits=None if unknown else 5 if cancelled else 6,
        elapsed_ms=0,
        unresolved_calls=1 if unknown else 0,
        reason="cancelled"
        if cancelled
        else "unknown_or_invalid_usage"
        if unknown
        else "calls_exhausted",
    )
    outcome = {
        k: first["identity"][k] for k in ("configuration", "binding", "candidate")
    }
    outcome.update(
        expected_heldout=copy.deepcopy(expected),
        state="compiled" if cancelled else "complete",
        usage=usage,
        report=None if cancelled else "a" * 64,
        status=None if cancelled else "blocked",
        persisted="f" * 64,
    )
    outcome.update(
        attempt_outcomes=[["settled", 5, 5]]
        if cancelled
        else [["settled", 5, 5], ["unknown", 1, 1]]
        if unknown
        else [["settled", 6, 6]],
        heldout_attempts=[]
        if cancelled
        else [
            dict(
                request={k: v for k, v in expected.items() if k != "content_sha256"},
                outcome="completed",
                content_sha256=expected["content_sha256"],
                safety=True,
                latency_ms=0,
                tokens=None if case == "unknown_tokens" else 1,
                cost_microunits=None if case == "unknown_cost" else 1,
            )
        ],
    )
    second = dict(
        phase="evaluate",
        pid=2,
        outcome=outcome,
        materialized=copy.deepcopy(first["materialized"]),
        authorization={"evaluation": True},
        new_dispatches=0 if cancelled else 1,
        predict_entries=0 if cancelled else 2 if case == "calls_exhausted" else 1,
        forbidden_framework=0,
        stages=[] if cancelled else ["baseline"],
        retry_preserved=True,
        network_attempts=0,
    )
    third = dict(
        phase="replay",
        pid=3,
        outcome=copy.deepcopy(outcome),
        materialized=copy.deepcopy(first["materialized"]),
        authorization={"evaluation": True},
        new_dispatches=0,
        framework_imports=0,
        network_attempts=0,
    )
    return first, second, third


class AdversarialHarnessTests(unittest.TestCase):
    def test_original_compile_validator_remains_mandatory(self) -> None:
        with (
            patch.object(h, "validate", side_effect=ValueError("original compile")),
            self.assertRaisesRegex(ValueError, "original compile"),
        ):
            h.validate_phases("cancelled", *phases("cancelled"))

    def test_four_case_oracles_and_mutations(self) -> None:
        with patch.object(h, "validate"):
            for case in h.CASES:
                base = phases(case)
                h.validate_phases(case, *base)
                mutations: Any = (
                    (1, "new_dispatches", 9),
                    (1, "predict_entries", 9),
                    (1, "forbidden_framework", 1),
                    (1, "retry_preserved", False),
                    (1, "stages", ["candidate"]),
                    (2, "new_dispatches", 1),
                    (2, "framework_imports", 1),
                    (2, "pid", 2),
                    (2, "network_attempts", 1),
                    (1, "authorization", {"evaluation": False}),
                    (2, "materialized", {}),
                )
                for phase, key, value in mutations:
                    values = copy.deepcopy(base)
                    values[phase][key] = value
                    with (
                        self.subTest(case=case, key=key),
                        self.assertRaises(ValueError),
                    ):
                        h.validate_phases(case, *values)
                for key, changed in (
                    ("calls", 0),
                    ("unresolved_calls", 9),
                    ("tokens", 0),
                    ("reason", None),
                ):
                    values = copy.deepcopy(base)
                    for phase in (1, 2):
                        values[phase]["outcome"]["usage"][key] = changed
                    with (
                        self.subTest(case=case, budget=key),
                        self.assertRaises(ValueError),
                    ):
                        h.validate_phases(case, *values)

    def test_source_oracle_is_not_the_native_runner(self) -> None:
        self.assertEqual(h.CHILD_SECONDS, 30)
        self.assertEqual(
            set(h.CASES),
            {"cancelled", "unknown_tokens", "unknown_cost", "calls_exhausted"},
        )
        sources = h.source_hashes()
        self.assertIn("tools/test_gepa_heldout_materialized.py", sources)
        self.assertIn("tools/test_gepa_heldout_adversarial.py", sources)
        self.assertIn("refinery/gepa_optimizer.py", sources)

    def test_explicit_case_cannot_change_legacy_suite(self) -> None:
        with patch.object(
            runtime,
            "verify_prepared",
            side_effect=AssertionError("must not execute"),
        ):
            for suite in ("reflection", "durable", "materialized", "heldout"):
                with self.assertRaisesRegex(ValueError, "case cannot alter"):
                    launch.execute("/unused", suite=suite, case="cancelled")
            for case in (None, "unknown", ""):
                with self.assertRaisesRegex(ValueError, "explicit adversarial case"):
                    launch.execute("/unused", suite="heldout-adversarial", case=case)

    def test_ledger_unknown_and_exhaustion_expectations_are_real(self) -> None:
        for case in h.CASES:
            budget = ev.Budget(
                ev.Limits(6 if case == "calls_exhausted" else 16, 100, 1000, 100),
                clock_ms=ev.VirtualClock(),
            )
            for _ in range(5):
                ticket = budget.reserve(ev.Reservation(10, 10))
                budget.settle(ticket, tokens=1, cost_microunits=1)
            if case == "cancelled":
                budget.stop("cancelled")
            else:
                ticket = budget.reserve(ev.Reservation(10, 10))
                if case.startswith("unknown_"):
                    with self.assertRaisesRegex(ev.Halt, "unknown_or_invalid_usage"):
                        budget.settle(
                            ticket,
                            tokens=None if case == "unknown_tokens" else 1,
                            cost_microunits=None if case == "unknown_cost" else 1,
                        )
                else:
                    budget.settle(ticket, tokens=1, cost_microunits=1)
                    with self.assertRaisesRegex(ev.Halt, "calls_exhausted"):
                        budget.reserve(ev.Reservation(10, 10))
            self.assertEqual(budget.snapshot(), phases(case)[1]["outcome"]["usage"])
            before = budget.snapshot()
            with self.assertRaises(ev.Halt):
                budget.reserve(ev.Reservation(10, 10))
            self.assertEqual(budget.snapshot(), before)

    def test_case_configuration_is_fixed_before_any_effect(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(base, "TETHER", Path(os.environ["RUNSPINDLE_TETHER_SOURCE"])),
        ):
            path = Path(tmp)
            with (
                base.signed_fixture(path),
                patch(
                    "refinery.tether_dependency.TetherDependency",
                    side_effect=lambda _: importlib.import_module(
                        "test_feedback_collection"
                    ).dependency(),
                ),
            ):
                configurations = []
                for case in h.CASES:
                    with base.materialized_context(path) as material:
                        session = h.session_for(material, case)
                        configurations.append(session.config)
                        self.assertEqual(
                            session.limits.calls, 6 if case == "calls_exhausted" else 16
                        )
                        self.assertEqual(len(session.provider.script), 7)
                        self.assertEqual(session.provider.calls, [])
                        self.assertEqual(
                            [c.tokens for c in session.provider.script[:5]], [1] * 5
                        )
                        sixth = session.provider.script[5]
                        self.assertEqual(
                            sixth.tokens, None if case == "unknown_tokens" else 1
                        )
                        self.assertEqual(
                            sixth.cost_microunits, None if case == "unknown_cost" else 1
                        )
                        session.guard("optimization")
                        session.guard("evaluation")
                        self.assertEqual(
                            base.authority_receipt(
                                material, ("optimization", "reflection", "evaluation")
                            ),
                            dict(optimization=True, reflection=True, evaluation=True),
                        )
                self.assertEqual(len(set(configurations)), 4)

    def test_launcher_case_mounts_and_unchanged_bound(self) -> None:
        inputs = {"synthetic": True}
        calls = []

        def command(argv: list[str], timeout: int) -> Any:
            calls.append((argv, timeout))
            if argv[:3] == ["docker", "image", "inspect"]:
                raw = runtime.source_bytes(
                    "docs/python313-image/docker29-inspect.json"
                ).decode()
            elif argv[:2] == ["docker", "run"]:
                raw = json.dumps(dict(inputs=inputs, case="unknown_tokens"))
            else:
                raw = ""
            return subprocess.CompletedProcess(argv, 0, raw, "")

        with (
            patch.object(launch, "materialized_inputs", return_value=inputs),
            patch.object(runtime, "verify_prepared"),
            patch.object(launch, "bounded", command),
            patch.object(h, "validate_report"),
        ):
            launch.execute(
                "/deps-input",
                suite="heldout-adversarial",
                case="unknown_tokens",
                tether="/source-input",
                git="/git-input",
            )
        argv, seconds = calls[1]
        self.assertEqual(seconds, 120)
        self.assertEqual(
            argv[-3:],
            ["tools/test_gepa_heldout_adversarial.py", "--case", "unknown_tokens"],
        )
        self.assertIn("/source-input:/tether:ro", argv)
        self.assertIn("/git-input:/usr/bin/git:ro", argv)
        self.assertEqual(calls[-1][0][:3], ["docker", "ps", "-a"])

    def test_child_failure_cleanup_and_exact_bound(self) -> None:
        paths = []
        closed = []

        @contextmanager
        def fixture(path: Path) -> Any:
            paths.append(path)
            try:
                yield
            finally:
                closed.append(True)

        with (
            patch.object(base, "signed_fixture", fixture),
            patch.object(launch, "materialized_inputs", return_value={}),
            patch.object(launch, "bounded", side_effect=TimeoutError) as bounded,
            self.assertRaises(TimeoutError),
        ):
            h.run("cancelled")
        self.assertEqual(bounded.call_args.args[1], 30)
        self.assertEqual(bounded.call_args.args[0][-1], "cancelled")
        self.assertEqual(
            bounded.call_args.kwargs["env"]["RUNSPINDLE_TETHER_SOURCE"], "/tether"
        )
        self.assertNotIn("LD_LIBRARY_PATH", bounded.call_args.kwargs["env"])
        self.assertEqual(closed, [True])
        self.assertFalse(paths[0].exists())

    def test_detached_compile_identity_is_denied(self) -> None:
        with patch.object(h, "validate"):
            for key in ("candidate", "configuration"):
                values = phases("cancelled")
                values[0]["identity"][key] = "9" * 64
                for phase in (1, 2):
                    values[phase]["outcome"][key] = "9" * 64
                with (
                    self.subTest(key=key),
                    self.assertRaisesRegex(
                        ValueError, "checkpoint/framework association"
                    ),
                ):
                    h.validate_phases("cancelled", *values)

    def test_complete_heldout_receipt_schema_and_binding(self) -> None:
        with patch.object(h, "validate"):
            for mutation in (
                "missing_content",
                "missing_output",
                "bool_cost",
                "training_output",
                "wrong_source",
                "wrong_split",
                "wrong_content",
            ):
                values = phases("unknown_tokens")
                for phase in (1, 2):
                    receipt = values[phase]["outcome"]["heldout_attempts"][0]
                    if mutation == "missing_content":
                        del receipt["content_sha256"]
                    elif mutation == "missing_output":
                        del receipt["request"]["output_id"]
                    elif mutation == "bool_cost":
                        receipt["cost_microunits"] = True
                    elif mutation == "training_output":
                        receipt["request"]["output_id"] = "train"
                    elif mutation == "wrong_source":
                        receipt["request"]["source_sha256"] = "a" * 64
                    elif mutation == "wrong_split":
                        receipt["request"]["split_sha256"] = "b" * 64
                    else:
                        receipt["content_sha256"] = "e" * 64
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    h.validate_phases("unknown_tokens", *values)

    def test_compile_receipt_digest_associations(self) -> None:
        with patch.object(h, "validate"):
            for target in ("identity", "framework"):
                values = phases("cancelled")
                values[0][target][
                    "attempts" if target == "identity" else "attempts_sha256"
                ] = "a" * 64
                with (
                    self.subTest(target=target),
                    self.assertRaisesRegex(ValueError, "receipt digest association"),
                ):
                    h.validate_phases("cancelled", *values)
