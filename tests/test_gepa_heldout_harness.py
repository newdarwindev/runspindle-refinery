"""New three-process oracle tests; never native/framework qualification."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import gepa_runtime as runtime
from tools import offline_gepa as launch
from tools import test_gepa_heldout_materialized as h


def phases() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    usage = dict(
        calls=5,
        tokens=5,
        cost_microunits=5,
        elapsed_ms=0,
        unresolved_calls=0,
        reason=None,
    )
    compile_identity: dict[str, Any] = {
        k: c * 64
        for k, c in zip(
            (
                "snapshot",
                "configuration",
                "binding",
                "candidate",
                "result",
                "attempts",
                "witnesses",
            ),
            "abcdefa",
        )
    }
    compile_identity.update(usage=usage, clock_ms=0)
    a: dict[str, Any] = dict(
        identity=compile_identity,
        persisted="f" * 64,
        network_attempts=0,
        framework=dict(
            candidate_sha256=compile_identity["candidate"],
            configuration_sha256=compile_identity["configuration"],
        ),
    )
    material = {
        p: dict(
            manifest="a" * 64,
            owner="b" * 64,
            registry="c" * 64,
            feedback="d" * 64,
            source=None,
        )
        for p in ("optimization", "evaluation")
    }
    a.update(
        phase="compile",
        pid=1,
        materialized=material,
        authorization={"optimization": True, "reflection": True},
    )
    ident = dict(
        candidate=a["identity"]["candidate"],
        configuration=a["identity"]["configuration"],
        binding=a["identity"]["binding"],
        report="a" * 64,
        attempts="b" * 64,
        clock_ms=0,
        state="complete",
        usage=dict(
            calls=7,
            tokens=7,
            cost_microunits=7,
            elapsed_ms=0,
            unresolved_calls=0,
            reason=None,
        ),
    )
    b = dict(
        phase="evaluate",
        pid=2,
        materialized=copy.deepcopy(material),
        identity=ident,
        persisted="e" * 64,
        authorization={"evaluation": True},
        training_after_seal_denied=True,
        new_dispatches=2,
        predict_entries=2,
        compile_entries=0,
        reflection_entries=0,
        stages=["baseline", "candidate"],
        scores={"baseline": 0, "candidate": 1},
        observations_match_receipts=True,
        candidate_matches_compile=True,
        network_attempts=0,
    )
    c = dict(
        phase="replay",
        pid=3,
        materialized=copy.deepcopy(material),
        identity=copy.deepcopy(ident),
        persisted=b["persisted"],
        authorization={"evaluation": True},
        training_after_seal_denied=True,
        new_dispatches=0,
        framework_imports=0,
        network_attempts=0,
        withdrawal_denied=True,
        withdrawal_preserved_checkpoint=True,
    )
    for phase, intent, checkpoint in (
        (a, "start", "a" * 64),
        (b, "resume_evaluation", a["persisted"]),
        (c, "resume_evaluation", b["persisted"]),
    ):
        phase["admission"] = dict(
            intent=intent,
            factory_entries=1,
            provider_entries=0,
            framework_entries=0,
            cursor_entries=0,
            before=checkpoint,
            after=checkpoint,
        )
    return a, b, c


class HeldoutHarnessTests(unittest.TestCase):
    def test_original_compile_oracle_and_phase_invariants(self) -> None:
        a, b, c = phases()
        with (
            patch.object(h, "validate", side_effect=ValueError("original oracle")),
            self.assertRaisesRegex(ValueError, "original oracle"),
        ):
            h.validate_phases(a, b, c)
        with patch.object(h, "validate"):
            h.validate_phases(a, b, c)
            mutations = (
                (b, "new_dispatches", 3),
                (b, "compile_entries", 1),
                (b, "reflection_entries", 1),
                (b, "candidate_matches_compile", False),
                (b, "training_after_seal_denied", False),
                (c, "framework_imports", 1),
                (c, "new_dispatches", 1),
                (c, "withdrawal_denied", False),
                (c, "withdrawal_preserved_checkpoint", False),
                (c, "pid", 2),
                (c, "persisted", "changed"),
            )
            for phase, key, value in mutations:
                changed = copy.deepcopy((a, b, c))
                changed[1 if phase is b else 2][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    h.validate_phases(*changed)
            for key in ("manifest", "owner", "registry", "feedback", "source"):
                altered = copy.deepcopy(c)
                altered["materialized"]["evaluation"][key] = "changed"
                with self.subTest(key=key), self.assertRaises(ValueError):
                    h.validate_phases(a, b, altered)

    def test_exact_owner_and_complete_identity_are_mandatory(self) -> None:
        a, b, c = phases()
        with patch.object(h, "validate"):
            for field in ("binding", "report", "attempts", "clock_ms", "usage"):
                b2, c2 = copy.deepcopy((b, c))
                b2["identity"].pop(field)
                c2["identity"].pop(field)
                with self.subTest(missing=field), self.assertRaises(ValueError):
                    h.validate_phases(a, b2, c2)
            b2, c2 = copy.deepcopy((b, c))
            b2["identity"]["binding"] = c2["identity"]["binding"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "Session owner"):
                h.validate_phases(a, b2, c2)
            for bad in ("short", "Z" * 64, 123):
                b2, c2 = copy.deepcopy((b, c))
                b2["identity"]["report"] = c2["identity"]["report"] = bad
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    h.validate_phases(a, b2, c2)
            b2, c2 = copy.deepcopy((b, c))
            b2["identity"]["usage"]["elapsed_ms"] = c2["identity"]["usage"][
                "elapsed_ms"
            ] = False
            with self.assertRaises(ValueError):
                h.validate_phases(a, b2, c2)

    def test_replay_import_guard_precedes_components_and_restores_meta_path(
        self,
    ) -> None:
        import builtins
        import sys

        class StopProbe(Exception):
            pass

        before = tuple(sys.meta_path)
        original = builtins.__import__
        observed = []

        def importing(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "refinery":
                try:
                    importlib.import_module("dspy")
                except ValueError:
                    observed.append("blocked before component import")
                raise StopProbe
            return original(name, *args, **kwargs)

        with (
            patch.object(builtins, "__import__", importing),
            self.assertRaises(StopProbe),
        ):
            h.replay_child(Path("/unused"), {}, [])
        self.assertEqual(observed, ["blocked before component import"])
        self.assertEqual(tuple(sys.meta_path), before)

    def test_children_bounded_clean_environment(self) -> None:
        for phase in ("compile", "evaluate", "replay"):
            with patch.object(
                launch,
                "bounded",
                return_value=subprocess.CompletedProcess([], 0, "{}", ""),
            ) as call:
                h.run_child(phase, Path("/tmp/synthetic"))
            self.assertEqual(call.call_args.args[1], 30)
            env = call.call_args.kwargs["env"]
            self.assertEqual(env["RUNSPINDLE_TETHER_SOURCE"], "/tether")
            self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
            self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_failure_closes_fixture_and_temporary_directory(self) -> None:
        seen = []
        closed = []

        @contextmanager
        def fixture(path: Path) -> Any:
            seen.append(path)
            try:
                yield
            finally:
                closed.append(True)

        with (
            patch.object(h, "signed_fixture", fixture),
            patch.object(launch, "materialized_inputs", return_value={}),
            patch.object(h, "run_child", side_effect=TimeoutError),
            self.assertRaises(TimeoutError),
        ):
            h.run()
        self.assertEqual(closed, [True])
        self.assertFalse(seen[0].exists())

    def test_new_lane_pins_mounts_and_outer_bound(self) -> None:
        inputs = {"synthetic": True}
        calls = []

        def command(argv: list[str], timeout: int) -> Any:
            calls.append((argv, timeout))
            if argv[:3] == ["docker", "image", "inspect"]:
                raw = runtime.source_bytes(
                    "docs/python313-image/docker29-inspect.json"
                ).decode()
            elif argv[:2] == ["docker", "run"]:
                raw = json.dumps({"inputs": inputs})
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
                "/deps-input", suite="heldout", tether="/source-input", git="/git-input"
            )
        argv, seconds = calls[1]
        self.assertEqual(seconds, 120)
        self.assertEqual(argv[-1], "tools/test_gepa_heldout_materialized.py")
        self.assertIn("/source-input:/tether:ro", argv)
        self.assertIn("/git-input:/usr/bin/git:ro", argv)
        self.assertEqual(calls[-1][0][:3], ["docker", "ps", "-a"])

    def test_source_bindings_include_all_used_fixture_modules(self) -> None:
        sources = h.source_hashes()
        for p in (
            "tools/test_gepa_heldout_materialized.py",
            "tools/test_gepa_materialized.py",
            "tools/test_gepa_reflection.py",
            "tests/test_materialized_durable.py",
            "tests/test_dataset_materialization.py",
            "tests/test_thumbs_up_collection.py",
            "refinery/gepa_optimizer.py",
            "refinery/durable_dspy.py",
        ):
            self.assertIn(p, sources)

    def test_real_signed_mixed_files_reopen_and_phase_authority(self) -> None:
        from refinery import evaluation as ev

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(h, "TETHER", Path(os.environ["RUNSPINDLE_TETHER_SOURCE"])),
        ):
            path = Path(temp)
            with (
                h.signed_fixture(path),
                patch(
                    "refinery.tether_dependency.TetherDependency",
                    side_effect=lambda _: importlib.import_module(
                        "test_feedback_collection"
                    ).dependency(),
                ),
            ):
                identities = []
                for index in range(2):
                    with h.materialized_context(path) as material:
                        session = h.session_for(material, intent="start")
                        session.guard("optimization")
                        session.guard("evaluation")
                        self.assertEqual(
                            h.authority_receipt(
                                material, ("optimization", "reflection", "evaluation")
                            ),
                            dict(optimization=True, reflection=True, evaluation=True),
                        )
                        self.assertEqual(len(session.provider.script), 7)
                        identities.append(
                            (
                                session.binding_sha256,
                                h.materialized_identity(session, material),
                            )
                        )
                        if index:
                            batch = material["batches"][1]
                            material["registry"].withdraw(
                                batch.selections[0].item_id,
                                approve=material["approve"],
                                permission_ref=h.REFERENCE,
                            )
                            with self.assertRaises((ValueError, ev.Halt)):
                                session.guard("evaluation")
                            self.assertEqual(session.provider.calls, [])
                self.assertEqual(identities[0], identities[1])
