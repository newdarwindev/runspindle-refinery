"""Admission observer/oracle tests, not actual framework execution."""

from __future__ import annotations

import copy
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery.durable_dspy import Session
from tools import test_gepa_heldout_materialized as h


class AdmissionHarnessTests(unittest.TestCase):
    def test_missing_effectful_or_changed_admission_receipt_denied(self) -> None:
        phases = importlib.import_module("test_gepa_heldout_harness").phases
        with patch.object(h, "validate"):
            for index in range(3):
                for field, value in (
                    ("factory_entries", 0),
                    ("factory_entries", True),
                    ("provider_entries", 1),
                    ("framework_entries", 1),
                    ("cursor_entries", 1),
                    ("after", "9" * 64),
                    ("intent", "inferred"),
                ):
                    values = copy.deepcopy(phases())
                    values[index]["admission"][field] = value
                    with (
                        self.subTest(phase=index, field=field),
                        self.assertRaises(ValueError),
                    ):
                        h.validate_phases(*values)
                values = copy.deepcopy(phases())
                values[index]["admission"] = {}
                with self.assertRaises((ValueError, KeyError)):
                    h.validate_phases(*values)

    def test_actual_factory_observer_and_bypass_effect_state_mutations(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(h, "TETHER", Path(os.environ["RUNSPINDLE_TETHER_SOURCE"])),
        ):
            path = Path(tmp)
            with (
                h.signed_fixture(path),
                patch(
                    "refinery.tether_dependency.TetherDependency",
                    side_effect=lambda _: importlib.import_module(
                        "test_feedback_collection"
                    ).dependency(),
                ),
                h.materialized_context(path) as material,
            ):
                original = h.session_for
                session, receipt = h.admitted_session(material, intent="start")
                h.validate_admission(
                    receipt, intent="start", checkpoint=h.persisted(material["store"])
                )
                self.assertIs(type(session), Session)
                self.assertEqual(session.provider.calls, [])
                with (
                    patch.object(h, "session_for", return_value=session),
                    self.assertRaisesRegex(ValueError, "factory bypassed"),
                ):
                    h.admitted_session(material, intent="start")

                with (
                    patch.object(
                        Session,
                        "materialized_learning",
                        staticmethod(lambda *args, **kwargs: session),
                    ),
                    self.assertRaisesRegex(
                        ValueError, "factory implementation replaced"
                    ),
                ):
                    h.admitted_session(material, intent="start")

                def substituted(m: Any, *, intent: str) -> Any:
                    original(m, intent=intent)
                    return session

                with (
                    patch.object(h, "session_for", substituted),
                    self.assertRaisesRegex(ValueError, "factory bypassed"),
                ):
                    h.admitted_session(material, intent="start")

                def effect(m: Any, *, intent: str) -> Any:
                    created = original(m, intent=intent)
                    created.provider.complete(
                        ev.Request(
                            "baseline", "out", "v1", "a" * 64, "b" * 64, "c" * 64, 1
                        )
                    )
                    return created

                with (
                    patch.object(h, "session_for", effect),
                    self.assertRaisesRegex(ValueError, "provider execution forbidden"),
                ):
                    h.admitted_session(material, intent="start")

                fake = SimpleNamespace(
                    GEPA=SimpleNamespace(compile=lambda: None),
                    Predict=SimpleNamespace(forward=lambda: None),
                )

                def framework_effect(m: Any, *, intent: str) -> Any:
                    created = original(m, intent=intent)
                    fake.GEPA.compile()
                    return created

                with (
                    patch.dict(sys.modules, {"dspy": fake}),
                    patch.object(h, "session_for", framework_effect),
                    self.assertRaisesRegex(ValueError, "framework execution forbidden"),
                ):
                    h.admitted_session(material, intent="start")

                def state_change(m: Any, *, intent: str) -> Any:
                    created = original(m, intent=intent)
                    with m["store"].transaction() as tx:
                        m["store"].put(
                            tx, "admission_probe", "probe", dict(changed=True)
                        )
                    return created

                with (
                    patch.object(h, "session_for", state_change),
                    self.assertRaisesRegex(ValueError, "changed checkpoint"),
                ):
                    h.admitted_session(material, intent="start")
