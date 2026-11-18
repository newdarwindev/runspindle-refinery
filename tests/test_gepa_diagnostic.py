"""Source-only diagnostic behavior; never substitutes for actual GEPA execution."""

from __future__ import annotations

import io
import json
import sys
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from tools import test_gepa_reflection as harness


class CompileDiagnosticTests(unittest.TestCase):
    def test_original_failure_is_preserved_and_bounded(self) -> None:
        error = ValueError(
            "synthetic /private/runtime/file.py " + "x" * 10000 + "\nsecret"
        )
        calls: list[object] = []
        instance = object()

        def fail(value: object) -> None:
            calls.append(value)
            raise error

        output = io.StringIO()
        with patch.object(sys, "__stderr__", output):
            with self.assertRaises(ValueError) as raised:
                harness.observe_compile(fail, instance)
        self.assertIs(raised.exception, error)
        self.assertEqual(calls, [instance])
        text = output.getvalue()
        self.assertLess(len(text), 4096)
        self.assertNotIn("/private", text)
        report = json.loads(text.removeprefix("GEPA_COMPILE_EXCEPTION "))
        self.assertEqual(report["exception_type"], "ValueError")
        self.assertLessEqual(len(report["message"]), 512)
        self.assertLessEqual(len(report["frames"]), 12)
        self.assertTrue(all("/" not in frame["file"] for frame in report["frames"]))

    def test_base_exception_and_broken_diagnostic_sink_preserve_failure(self) -> None:
        error = KeyboardInterrupt("synthetic interruption")

        def fail(_: object) -> None:
            raise error

        with patch("builtins.print", side_effect=RuntimeError("broken sink")):
            with self.assertRaises(KeyboardInterrupt) as raised:
                harness.observe_compile(fail, object())
        self.assertIs(raised.exception, error)

    def test_success_is_transparent_and_silent(self) -> None:
        result = object()
        with patch("builtins.print") as emit:
            self.assertIs(harness.observe_compile(lambda _: result, object()), result)
        emit.assert_not_called()

    def test_unknown_argument_repr_is_not_invoked(self) -> None:
        class Hostile:
            def __str__(self) -> str:
                raise AssertionError("argument callback")

        report = harness.compile_diagnostic(ValueError(Hostile()))
        self.assertEqual(report["message"], "")


class MemoryObservationTests(unittest.TestCase):
    def test_compile_return_and_inspection_are_distinct(self) -> None:
        events: list[dict[str, Any]] = []
        result = SimpleNamespace(
            predict=SimpleNamespace(signature=SimpleNamespace(instructions="synthetic"))
        )
        returned = harness.observe_compile(
            lambda _: result, object(), observations=events
        )
        self.assertIs(returned, result)
        self.assertEqual(harness.inspect_compile_result(returned, events), "synthetic")
        self.assertEqual(
            [e["stage"] for e in events],
            [
                "original_compile_entry",
                "original_compile_return",
                "wrapper_inspection_return",
            ],
        )
        broken: list[dict[str, Any]] = []
        returned = harness.observe_compile(
            lambda _: None, object(), observations=broken
        )
        with self.assertRaises(AttributeError):
            harness.inspect_compile_result(returned, broken)
        self.assertEqual(
            [e["stage"] for e in broken],
            [
                "original_compile_entry",
                "original_compile_return",
                "wrapper_inspection_exception",
            ],
        )

    def test_memory_survives_unavailable_stderr(self) -> None:
        events: list[dict[str, Any]] = []
        failure = ValueError("synthetic failure")

        def original(_: Any) -> Any:
            raise failure

        with patch("builtins.print", side_effect=OSError("closed sink")):
            with self.assertRaises(ValueError) as raised:
                harness.observe_compile(original, object(), observations=events)
        self.assertIs(raised.exception, failure)
        self.assertEqual(
            [e["stage"] for e in events],
            ["original_compile_entry", "original_compile_exception"],
        )
        self.assertEqual(events[-1]["exception"]["message"], "synthetic failure")

    def test_diagnostic_capture_failure_does_not_replace_original(self) -> None:
        failure = ValueError("synthetic failure")

        def original(_: Any) -> Any:
            raise failure

        events: list[dict[str, Any]] = []
        with (
            patch.object(
                harness,
                "compile_diagnostic",
                side_effect=RuntimeError("capture failed"),
            ),
            patch("builtins.print"),
        ):
            with self.assertRaises(ValueError) as raised:
                harness.observe_compile(original, object(), observations=events)
        self.assertIs(raised.exception, failure)
        self.assertEqual(events[-1]["exception"], {"diagnostic_capture_failed": True})

    @staticmethod
    def synthetic_function(source: str) -> Any:
        namespace: dict[str, Any] = {}
        # A diagnostic-helper fixture, not a replacement framework or optimizer run.
        exec(
            compile(
                source, str(harness.ROOT / "refinery" / "gepa_optimizer.py"), "exec"
            ),
            namespace,
        )
        return namespace["optimize"]

    def test_exact_scope_catches_swallowed_exceptions_with_bounded_memory(self) -> None:
        function = self.synthetic_function(
            "def optimize():\n for i in range(30):\n  try: raise ValueError('synthetic')\n  except ValueError: pass\n return 17\n"
        )
        events: list[dict[str, Any]] = []
        previous = sys.gettrace()
        with harness.observe_optimizer_exceptions(function, events):
            self.assertEqual(function(), 17)
        self.assertIs(sys.gettrace(), previous)
        self.assertEqual(len(events), 12)
        self.assertTrue(all(e["stage"] == "optimizer_exception_event" for e in events))
        self.assertTrue(
            all(e["exception"]["exception_type"] == "ValueError" for e in events)
        )
        self.assertLess(len(json.dumps(events)), 32768)

    def test_previous_trace_restored_on_exception_and_other_frames_ignored(
        self,
    ) -> None:
        function = self.synthetic_function(
            "def optimize(callback):\n callback()\n return 23\n"
        )

        def unrelated() -> None:
            try:
                raise ValueError("unrelated")
            except ValueError:
                pass

        def previous(frame: Any, event: str, arg: Any) -> Any:
            return previous

        saved = sys.gettrace()
        events: list[dict[str, Any]] = []
        try:
            sys.settrace(previous)
            with self.assertRaisesRegex(RuntimeError, "outside"):
                with harness.observe_optimizer_exceptions(function, events):
                    self.assertEqual(function(unrelated), 23)
                    raise RuntimeError("outside")
            self.assertIs(sys.gettrace(), previous)
            self.assertEqual(events, [])
        finally:
            sys.settrace(saved)

    def test_wrong_trace_target_rejected_without_changing_trace(self) -> None:
        previous = sys.gettrace()
        with self.assertRaisesRegex(ValueError, "trace target"):
            with harness.observe_optimizer_exceptions(lambda: None, []):
                self.fail("unexpected target admitted")
        self.assertIs(sys.gettrace(), previous)
