"""Synthetic adapter seam only; does not import or qualify DSPy."""

import contextlib
import sys
import types
import unittest
from unittest.mock import patch

from refinery.dspy_local import LocalDSPyProvider
from refinery.evaluation import Completion, Request, VirtualClock


class LocalAdapterContractTests(unittest.TestCase):
    def exercise(self, *, explicit_retry=False):
        observed = {}
        original = RuntimeError("synthetic adapter failure")

        class BaseLM:
            def __init__(self, **kwargs):
                observed["lm_configuration"] = kwargs

        class ChatAdapter:
            def __init__(
                self,
                *,
                use_native_function_calling=False,
                use_json_adapter_fallback=True,
            ):
                observed["fallback"] = use_json_adapter_fallback
                observed["native_tools"] = use_native_function_calling

        @contextlib.contextmanager
        def context(**kwargs):
            observed.update(kwargs)
            yield

        class Predict:
            def __init__(self, signature):
                observed["signature"] = signature

            def __call__(self, **inputs):
                lm = observed["lm"]
                lm.forward(messages=[{"role": "user", "content": inputs["case"]}])
                # Model the documented default retry seam without importing DSPy.
                if explicit_retry or observed["fallback"]:
                    lm.forward(messages=[])
                raise original

        fake = types.SimpleNamespace(
            __version__="3.1.0",
            BaseLM=BaseLM,
            ChatAdapter=ChatAdapter,
            context=context,
            Predict=Predict,
        )
        request = Request(
            "baseline", "synthetic", "revision", "a" * 64, "b" * 64, "c" * 64, 1
        )
        provider = LocalDSPyProvider(
            (Completion(b"synthetic", 1, 0, True),),
            clock=VirtualClock(),
            dependency_sha256="d" * 64,
        )
        with patch.dict(sys.modules, {"dspy": fake}):
            with self.assertRaises(
                RuntimeError if not explicit_retry else ValueError
            ) as error:
                provider.complete(request)
        self.assertFalse(observed["fallback"])
        self.assertFalse(observed["native_tools"])
        self.assertTrue(observed["disable_history"])
        self.assertFalse(observed["lm_configuration"]["cache"])
        self.assertEqual(provider.calls, [request])
        self.assertEqual(len(provider.lm_calls), 1)
        if explicit_retry:
            self.assertEqual(str(error.exception), "unexpected unbudgeted DSPy retry")
        else:
            self.assertIs(error.exception, original)

    def test_adapter_failure_propagates_without_implicit_retry(self):
        self.exercise()

    def test_explicit_second_call_still_fails_closed(self):
        self.exercise(explicit_retry=True)
