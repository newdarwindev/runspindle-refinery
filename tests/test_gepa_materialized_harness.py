"""Oracle/orchestration and signed-store tests, never framework qualification."""

from __future__ import annotations

import copy
import importlib
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
from tools import test_gepa_materialized as h


def receipts() -> tuple[dict[str, Any], dict[str, Any]]:
    a, b = importlib.import_module("test_gepa_durable_harness").receipts()
    material = dict(
        manifest="a" * 64,
        permission="b" * 64,
        owner="c" * 64,
        registry="d" * 64,
        feedback="e" * 64,
        source=None,
        tether="1e8d2091c55dfad47fdc64ec2e73fcd946da9921",
    )
    for value in (a, b):
        value["materialized"] = copy.deepcopy(material)
        value["authorization"] = {"optimization": True, "reflection": True}
    b.update(withdrawal_denied=True, withdrawal_preserved_checkpoint=True)
    return a, b


class MaterializedHarnessTests(unittest.TestCase):
    def test_physical_identity_and_grants_cannot_be_ignored(self) -> None:
        a, b = receipts()
        with patch.object(h, "validate"):
            h.validate_pair(a, b)
            for field in a["materialized"]:
                altered = copy.deepcopy(b)
                altered["materialized"][field] = "changed"
                with self.subTest(field=field), self.assertRaises(ValueError):
                    h.validate_pair(a, altered)
            for field in ("withdrawal_denied", "withdrawal_preserved_checkpoint"):
                altered = copy.deepcopy(b)
                altered[field] = False
                with self.subTest(field=field), self.assertRaises(ValueError):
                    h.validate_pair(a, altered)
            for phase in ("optimization", "reflection"):
                altered = copy.deepcopy(b)
                altered["authorization"][phase] = False
                with self.subTest(phase=phase), self.assertRaises(ValueError):
                    h.validate_pair(a, altered)

    def test_original_framework_oracle_and_zero_effects_remain_required(self) -> None:
        a, b = receipts()
        with patch.object(h, "validate", side_effect=ValueError("strict oracle")):
            with self.assertRaisesRegex(ValueError, "strict oracle"):
                h.validate_pair(a, b)
        with patch.object(h, "validate"):
            for field in ("new_dispatches", "framework_imports", "network_attempts"):
                changed = copy.deepcopy(b)
                changed[field] = 1
                with self.subTest(field=field), self.assertRaises(ValueError):
                    h.validate_pair(a, changed)

    def test_children_keep_reviewed_bounds_and_fixed_tether(self) -> None:
        with patch.object(
            launch, "bounded", return_value=subprocess.CompletedProcess([], 0, "{}", "")
        ) as command:
            h.run_child("compile", Path("/tmp/example"))
        self.assertEqual(command.call_args.args[1], 50)
        env = command.call_args.kwargs["env"]
        self.assertEqual(env["RUNSPINDLE_TETHER_SOURCE"], "/tether")
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_fixture_and_job_cleanup_on_child_failure(self) -> None:
        seen = []
        cleaned = []

        @contextmanager
        def fixture(path: Path) -> Any:
            seen.append(path)
            try:
                yield
            finally:
                cleaned.append(True)

        with (
            patch.object(h, "signed_fixture", fixture),
            patch.object(launch, "materialized_inputs", return_value={}),
            patch.object(h, "run_child", side_effect=TimeoutError),
            self.assertRaises(TimeoutError),
        ):
            h.run()
        self.assertEqual(cleaned, [True])
        self.assertFalse(seen[0].exists())

    def test_source_inventory_contains_signed_fixture_and_runtime(self) -> None:
        paths = h.source_hashes()
        for name in (
            "materialized_use",
            "readonly_view",
            "dataset_batch",
            "dataset_materialization",
            "dataset_contract",
            "feedback_projection",
            "feedback_use",
            "source_feedback",
            "tether_dependency",
        ):
            self.assertIn("refinery/" + name + ".py", paths)
        for name in (
            "materialized_durable",
            "dataset_materialization",
            "feedback_projection",
            "thumbs_up_collection",
            "feedback_collection",
        ):
            self.assertIn("tests/test_" + name + ".py", paths)

    def test_explicit_inputs_cannot_change_legacy_lanes(self) -> None:
        for kwargs in (
            {"suite": "materialized"},
            {"suite": "reflection", "tether": "/tether"},
            {"suite": "durable", "git": "/git"},
        ):
            with (
                patch.object(runtime, "verify_prepared") as check,
                self.assertRaises(ValueError),
            ):
                launch.execute("/unused", **kwargs)
            check.assert_not_called()

    def test_regular_canonical_git_input_is_required_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file = root / "git"
            file.write_bytes(b"not real git")
            file.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "Git binary"):
                launch.materialized_inputs(str(root), str(file))
            alias = root / "alias"
            alias.symlink_to(file)
            with self.assertRaises(ValueError):
                launch.materialized_inputs(str(root), str(alias))
            with self.assertRaises(ValueError):
                launch.canonical_input(str(root / ".." / root.name), directory=True)

    def test_new_lane_mounts_only_explicit_inputs_readonly(self) -> None:
        import json

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
                "/deps-input",
                suite="materialized",
                tether="/source-input",
                git="/git-input",
            )
        argv, seconds = calls[1]
        self.assertEqual(seconds, 120)
        self.assertIn("/source-input:/tether:ro", argv)
        self.assertIn("/git-input:/usr/bin/git:ro", argv)
        self.assertIn("RUNSPINDLE_TETHER_SOURCE=/tether", argv)
        self.assertIn("--network", argv)
        self.assertEqual(argv[-1], "tools/test_gepa_materialized.py")
        self.assertFalse(any("LD_LIBRARY_PATH" in v for v in argv))
        self.assertEqual(calls[-1][0][:3], ["docker", "ps", "-a"])

    def test_actual_signed_files_reopen_same_owner_and_current_withdrawal(self) -> None:
        from refinery import evaluation as ev
        from refinery.durable_dspy import Session
        from refinery.storage import VERSION

        source = Path(os.environ["RUNSPINDLE_TETHER_SOURCE"])
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(h, "TETHER", source),
        ):
            path = Path(directory)
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
                    with h.materialized_context(path) as m:
                        s = Session(
                            m["store"],
                            VERSION,
                            "signed-fixture",
                            m["snapshot"],
                            provider=ev.ScriptedProvider(
                                (ev.Completion(b"Synthetic answer", 1, 1, True, 0),),
                                clock=ev.VirtualClock(),
                            ),
                            limits=ev.Limits(8, 100, 100, 100),
                            per_call=ev.Reservation(10, 10),
                            budget_id="fixture-budget",
                            strategy="gepa",
                            training_output_id=m["store"]
                            .lineage(m["snapshot"])[0]
                            .examples[0]
                            .output.output_id,
                            retained_use=m["use"],
                            materialized_batches=(m["batch"],),
                        )
                        s.guard("optimization")
                        self.assertEqual(
                            h.authority_receipt(m),
                            {"optimization": True, "reflection": True},
                        )
                        identities.append(
                            (s.binding_sha256, h.materialized_identity(s, m))
                        )
                        if index:
                            m["registry"].withdraw(
                                m["item_id"],
                                approve=m["approve"],
                                permission_ref=h.REFERENCE,
                            )
                            with self.assertRaises((ValueError, ev.Halt)):
                                s.guard("optimization")
                            self.assertEqual(s.provider.calls, [])
                self.assertEqual(identities[0], identities[1])
