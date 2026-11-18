"""Synthetic orchestration/oracle tests, not native framework acceptance."""

from __future__ import annotations

import builtins
import copy
import importlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from tools import offline_gepa as launch
from tools import test_gepa_durable as h


def receipts() -> tuple[dict[str, Any], dict[str, Any]]:
    # Native framework report validator is separately tested unchanged.
    identity = {
        "candidate": "c",
        "configuration": "f",
        "attempts": "a",
        "witnesses": "w",
        "usage": {"calls": 5},
    }
    first = {
        "phase": "compile",
        "pid": 100,
        "network_attempts": 0,
        "persisted": "p",
        "identity": identity,
        "framework": {"candidate_sha256": "c", "configuration_sha256": "f"},
    }
    second = {
        "phase": "recover",
        "pid": 200,
        "network_attempts": 0,
        "new_dispatches": 0,
        "framework_imports": 0,
        "persisted": "p",
        "identity": copy.deepcopy(identity),
    }
    return first, second


class DurableHarnessTests(unittest.TestCase):
    def test_pair_rejects_same_process_changed_bytes_and_effects(self) -> None:
        a, b = receipts()
        with patch.object(h, "validate"):
            h.validate_pair(a, b)
            for key, value in [
                ("pid", 100),
                ("phase", "compile"),
                ("persisted", "other"),
                ("new_dispatches", 1),
                ("framework_imports", 1),
                ("network_attempts", 1),
            ]:
                altered = copy.deepcopy(b)
                altered[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    h.validate_pair(a, altered)
            for key in a["identity"]:
                altered = copy.deepcopy(b)
                altered["identity"][key] = "changed"
                with self.subTest(identity=key), self.assertRaises(ValueError):
                    h.validate_pair(a, altered)
            altered = copy.deepcopy(a)
            altered["framework"]["candidate_sha256"] = "other"
            with self.assertRaises(ValueError):
                h.validate_pair(altered, b)

    def test_original_framework_validator_is_mandatory(self) -> None:
        a, b = receipts()
        with patch.object(
            h, "validate", side_effect=ValueError("oracle broken")
        ) as oracle:
            with self.assertRaisesRegex(ValueError, "oracle broken"):
                h.validate_pair(a, b)
        oracle.assert_called_once_with(a["framework"])

    def test_child_uses_fixed_bound_sanitized_environment(self) -> None:
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(
                launch,
                "bounded",
                return_value=subprocess.CompletedProcess([], 0, "{}", ""),
            ) as command,
        ):
            self.assertEqual(h.run_child("compile", Path(d)), {})
            argv, timeout = command.call_args.args
            self.assertEqual(timeout, 50)
            self.assertEqual(argv[-3:], ["--child", "compile", d])
            env = command.call_args.kwargs["env"]
            self.assertEqual(env["PYTHONPATH"], "/deps:/work")
            self.assertEqual(
                set(env),
                {
                    "PATH",
                    "PYTHONPATH",
                    "PYTHONDONTWRITEBYTECODE",
                    "PYTHONHASHSEED",
                    "HOME",
                    "DSPY_CACHEDIR",
                    "HF_HUB_OFFLINE",
                    "LITELLM_LOCAL_MODEL_COST_MAP",
                    "LC_ALL",
                    "TZ",
                },
            )

    def test_child_nonzero_malformed_and_timeout_fail(self) -> None:
        for result in (
            subprocess.CompletedProcess([], 1, "{}", "failure"),
            subprocess.CompletedProcess([], 0, "not json", ""),
            subprocess.CompletedProcess([], 0, "[]", ""),
        ):
            with (
                patch.object(launch, "bounded", return_value=result),
                self.assertRaises(ValueError),
            ):
                h.run_child("compile", Path("/tmp"))
        with (
            patch.object(launch, "bounded", side_effect=TimeoutError),
            self.assertRaises(TimeoutError),
        ):
            h.run_child("compile", Path("/tmp"))

    def test_temporary_store_removed_on_second_child_failure(self) -> None:
        seen = []

        def fake(phase: str, path: Path) -> dict[str, Any]:
            seen.append(path)
            if phase == "recover":
                raise ValueError("recovery failed")
            return receipts()[0]

        with (
            patch.object(h, "run_child", fake),
            patch.object(h, "source_hashes", return_value={}),
            self.assertRaisesRegex(ValueError, "recovery failed"),
        ):
            h.run()
        self.assertEqual(len(seen), 2)
        self.assertFalse(seen[0].exists())

    def test_import_guard_forbids_both_frameworks_not_component(self) -> None:
        guard = h.RejectFramework()
        for name in ("dspy", "dspy.predict", "gepa", "gepa.core"):
            with self.assertRaises(ValueError):
                guard.find_spec(name)
        self.assertIsNone(guard.find_spec("refinery.gepa_codec"))
        self.assertEqual(len(guard.attempts), 4)

    def test_bounded_cleanup_kills_and_reaps_on_selector_failure(self) -> None:
        from unittest.mock import MagicMock

        process = MagicMock()
        process.poll.return_value = None
        with (
            patch("tools.offline_gepa.subprocess.Popen", return_value=process),
            patch(
                "tools.offline_gepa.selectors.DefaultSelector",
                side_effect=ValueError("pipes"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "pipes"):
                launch.bounded(["synthetic"], 1, env={})
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_unknown_suite_rejected_before_runtime(self) -> None:
        with (
            patch("tools.offline_gepa.runtime.verify_prepared") as verify,
            self.assertRaises(ValueError),
        ):
            launch.execute("/unused", suite="other")
        verify.assert_not_called()


class RecoveryBoundaryTests(unittest.TestCase):
    def test_swallowed_constructor_provider_entry_is_still_rejected(self) -> None:
        fixture: Any = importlib.import_module("test_durable")

        from refinery.durable_dspy import Session
        from refinery.storage import Store

        entries: list[bool] = []

        def construct(*args: Any, **kwargs: Any) -> Any:
            entries.append(True)
            try:
                kwargs["provider"].complete(None)
            except ValueError:
                pass  # A swallowed forbidden entry must still invalidate recovery.
            return SimpleNamespace(compile=lambda: object())

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with Store(path / "store.sqlite") as store:
                snapshot, _, _ = fixture.setup(store)
                first = {"snapshot": snapshot, "persisted": h.persisted(store)}
            with (
                patch("refinery.durable_dspy.Session", side_effect=construct),
                patch.object(h, "identity", return_value={}),
                self.assertRaisesRegex(ValueError, "recovery attempted effects/import"),
            ):
                h.recover_child(path, first, [])
        self.assertEqual(entries, [True])
        # The patch must restore the actual constructor after the negative fixture.
        from refinery import durable_dspy

        self.assertIs(durable_dspy.Session, Session)

    def test_checkpoint_digest_covers_every_retained_table(self) -> None:
        tables = (
            "objects",
            "families",
            "budgets",
            "experiments",
            "attempts",
            "environments",
            "activations",
        )
        with closing(sqlite3.connect(":memory:")) as db:
            for table in tables:
                db.execute("CREATE TABLE " + table + " (identity TEXT, payload TEXT)")
            store = SimpleNamespace(db=db)
            baseline = h.persisted(store)
            for table in tables:
                with self.subTest(table=table):
                    db.execute(
                        "INSERT INTO " + table + " VALUES(?,?)", ("row", "changed")
                    )
                    self.assertNotEqual(h.persisted(store), baseline)
                    db.execute("DELETE FROM " + table)
                    self.assertEqual(h.persisted(store), baseline)

    def test_identity_hashes_real_sqlite_witness_rows_as_supported_tuples(self) -> None:
        from refinery.gepa_evidence import GEPACompiled

        # Serialization fixture only, deliberately not a valid optimization proof.
        result = GEPACompiled(
            "compiled", None, "config", b"candidate", "candidate", (), (("calls", 5),)
        )
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute(
                "CREATE TABLE objects(kind TEXT, identity TEXT, digest TEXT, payload BLOB)"
            )
            for number in range(5):
                db.execute(
                    "INSERT INTO objects VALUES(?,?,?,?)",
                    ("gepa_request_witness", str(number), "digest", b"payload"),
                )
            session = SimpleNamespace(
                store=SimpleNamespace(db=db),
                snapshot_sha256="snapshot",
                config="config",
                binding_sha256="binding",
                clock=lambda: 0,
            )
            before = h.identity(session, result)
            db.execute(
                "UPDATE objects SET payload=? WHERE identity=?", (b"changed", "0")
            )
            self.assertNotEqual(
                h.identity(session, result)["witnesses"], before["witnesses"]
            )

    def test_swallowed_component_import_attempt_is_observed_and_guard_restored(
        self,
    ) -> None:
        fixture: Any = importlib.import_module("test_durable")
        from refinery.storage import Store

        original_import = builtins.__import__
        attempted: list[bool] = []
        previous = tuple(sys.meta_path)

        def importing(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "refinery.durable_dspy" and not attempted:
                attempted.append(True)
                try:
                    original_import("dspy")
                except (ValueError, ImportError):
                    pass  # A component cannot hide a forbidden import attempt.
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with Store(path / "store.sqlite") as store:
                snapshot, _, _ = fixture.setup(store)
                first = {"snapshot": snapshot, "persisted": h.persisted(store)}
            with (
                patch(
                    "refinery.durable_dspy.Session",
                    return_value=SimpleNamespace(compile=lambda: object()),
                ),
                patch.object(h, "identity", return_value={}),
                patch.object(builtins, "__import__", importing),
                self.assertRaisesRegex(ValueError, "recovery attempted effects/import"),
            ):
                h.recover_child(path, first, [])
        self.assertEqual(attempted, [True])
        self.assertEqual(tuple(sys.meta_path), previous)
