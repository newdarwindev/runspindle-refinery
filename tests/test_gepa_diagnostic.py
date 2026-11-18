"""Source-only diagnostic behavior; never substitutes for actual GEPA execution."""

from __future__ import annotations

import io
import json
import sys
import unittest
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
