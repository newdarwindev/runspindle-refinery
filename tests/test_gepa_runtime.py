"""Source/closure/launcher boundary tests. No Docker or GEPA execution."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from refinery import gepa_runtime as runtime
from tools import offline_gepa as launcher

harness: Any = importlib.import_module("tools.test_gepa_reflection")


class RuntimeTests(unittest.TestCase):
    def test_reviewed_closure_exact_inventory_and_modes(self) -> None:
        inventory, modes = runtime.reviewed()
        self.assertEqual(len(inventory["installed_files"]), 11877)
        self.assertEqual(len(modes), 14480)
        self.assertEqual(
            modes["."], {"type": "directory", "mode": 0o755, "sha256": None}
        )
        self.assertEqual(inventory["python"], "3.13.13")

    def test_altered_adoption_and_every_bound_source_fail_before_docker(self) -> None:
        original = runtime.source_bytes
        for target in runtime.FILES | {"offline/python313/gepa-adoption.json"}:

            def altered(path: str) -> bytes:
                return original(path) + (b" " if path == target else b"")

            with (
                self.subTest(target=target),
                patch.object(runtime, "source_bytes", altered),
                patch.object(launcher, "bounded") as command,
            ):
                with self.assertRaises(ValueError):
                    launcher.execute("/unused")
                command.assert_not_called()

    def test_exact_classic_and_observed_docker29_identity(self) -> None:
        observed = json.loads(
            runtime.source_bytes("docs/python313-image/docker29-inspect.json")
        )
        runtime.verify_image(observed)
        classic = copy.deepcopy(observed)
        classic[0]["Id"] = runtime.IMAGE_CONFIG
        del classic[0]["Descriptor"]
        runtime.verify_image(classic)
        classic[0]["RepoDigests"] = ["python@sha256:" + "b" * 64]
        with self.assertRaisesRegex(ValueError, "manifest reference changed"):
            runtime.verify_image(classic)
        classic[0].pop("RepoDigests")
        with self.assertRaisesRegex(ValueError, "manifest reference changed"):
            runtime.verify_image(classic)
        # A manifest-style ID without its descriptor is not a fallback.
        del observed[0]["Descriptor"]
        with self.assertRaisesRegex(ValueError, "descriptor changed"):
            runtime.verify_image(observed)

    def test_image_descriptor_rootfs_and_runtime_configuration_tampering(self) -> None:
        original = json.loads(
            runtime.source_bytes("docs/python313-image/docker29-inspect.json")
        )
        changes = [
            ("Id", "sha256:" + "a" * 64),
            ("Architecture", "amd64"),
            ("Os", "windows"),
            ("Variant", "wrong"),
        ]
        value: Any
        for field, value in changes:
            changed = copy.deepcopy(original)
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.verify_image(changed)
        for field, value in (
            ("digest", runtime.IMAGE_CONFIG),
            ("size", 1755),
            ("mediaType", "application/vnd.oci.image.index.v1+json"),
        ):
            changed = copy.deepcopy(original)
            changed[0]["Descriptor"][field] = value
            with (
                self.subTest(descriptor=field),
                self.assertRaisesRegex(ValueError, "descriptor changed"),
            ):
                runtime.verify_image(changed)
        changed = copy.deepcopy(original)
        changed[0]["RootFS"]["Layers"].reverse()
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            runtime.verify_image(changed)
        for field, value in (
            ("Entrypoint", ["sh"]),
            ("Env", ["PYTHONPATH=/foreign"]),
            ("Cmd", ["false"]),
            ("User", "different"),
            ("Volumes", {"/usr/local": {}}),
        ):
            changed = copy.deepcopy(original)
            changed[0]["Config"][field] = value
            with (
                self.subTest(config=field),
                self.assertRaisesRegex(ValueError, "configuration changed"),
            ):
                runtime.verify_image(changed)

    def test_snapshot_binds_files_and_directory_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package").mkdir()
            # Make the baseline distinct from the 0700 mutation under any umask.
            (root / "package").chmod(0o755)
            (root / "package" / "module.py").write_bytes(b"source")
            expected = runtime.snapshot(root)
            with patch.object(runtime, "reviewed", return_value=({}, expected)):
                self.assertEqual(runtime.verify_prepared(root), runtime.ADOPTION_SHA256)
                (root / "package").chmod(0o700)
                with self.assertRaisesRegex(ValueError, "modes differ"):
                    runtime.verify_prepared(root)
                (root / "package").chmod(expected["package"]["mode"])
                (root / "package" / "module.py").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "bytes or modes"):
                    runtime.verify_prepared(root)

    def test_snapshot_rejects_alias_special_extra_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "file"
            file.write_bytes(b"x")
            extra = root / "extra"
            for kind in ("symlink", "hardlink", "fifo", "bytecode"):
                if kind == "symlink":
                    extra.symlink_to(file)
                elif kind == "hardlink":
                    os.link(file, extra)
                elif kind == "fifo":
                    os.mkfifo(extra)
                else:
                    extra = root / "extra.pyc"
                    extra.write_bytes(b"x")
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    runtime.snapshot(root)
                extra.unlink()
            alias = root / "alias"
            alias.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                runtime.snapshot(alias)

    def test_runtime_target_precedes_import_and_files(self) -> None:
        with (
            patch.object(sys, "version_info", (3, 14, 0)),
            patch.object(runtime, "verify_prepared") as verify,
            patch("refinery.gepa_runtime.importlib.util.find_spec") as spec,
        ):
            with self.assertRaisesRegex(ValueError, "unqualified GEPA"):
                runtime.runtime_identity()
            verify.assert_not_called()
            spec.assert_not_called()

    def test_image_mismatch_and_cleanup_fail_closed(self) -> None:
        with (
            patch.object(runtime, "verify_prepared"),
            patch.object(
                launcher,
                "bounded",
                return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps([{"Id": "wrong"}]), ""
                ),
            ) as command,
        ):
            with self.assertRaisesRegex(ValueError, "image configuration"):
                launcher.execute("/prepared")
            self.assertEqual(command.call_count, 1)
        image = runtime.source_bytes(
            "docs/python313-image/docker29-inspect.json"
        ).decode()
        calls: list[tuple[list[str], int]] = []

        def fake(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            calls.append((argv, timeout))
            if argv[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(argv, 0, image, "")
            if argv[:2] == ["docker", "run"]:
                raise TimeoutError("synthetic timeout")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with (
            patch.object(runtime, "verify_prepared"),
            patch.object(launcher, "bounded", fake),
        ):
            with self.assertRaises(TimeoutError):
                launcher.execute("/prepared")
        self.assertEqual(
            [a[:2] for a, _ in calls],
            [
                ["docker", "image"],
                ["docker", "run"],
                ["docker", "rm"],
                ["docker", "ps"],
            ],
        )
        argv, timeout = calls[1]
        self.assertEqual(timeout, 120)
        for pair in (
            ("--network", "none"),
            ("--pull", "never"),
            ("--platform", "linux/arm64"),
            ("--cap-drop", "ALL"),
        ):
            self.assertEqual(argv[argv.index(pair[0]) + 1], pair[1])
        self.assertIn("--read-only", argv)
        self.assertIn(runtime.IMAGE, argv)
        self.assertIn("/usr/local/bin/python3.13", argv)

    def test_cleanup_requires_positive_empty_scoped_query(self) -> None:
        image = runtime.source_bytes(
            "docs/python313-image/docker29-inspect.json"
        ).decode()
        for code, output in ((1, ""), (0, "remaining-id\n")):
            calls: list[list[str]] = []

            def fake(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
                calls.append(argv)
                if argv[:3] == ["docker", "image", "inspect"]:
                    return subprocess.CompletedProcess(argv, 0, image, "")
                if argv[:2] == ["docker", "run"]:
                    return subprocess.CompletedProcess(
                        argv, 0, '{"status":"passed"}', ""
                    )
                if argv[:2] == ["docker", "ps"]:
                    return subprocess.CompletedProcess(
                        argv, code, output, "daemon unavailable" if code else ""
                    )
                return subprocess.CompletedProcess(
                    argv, 1, "", "already removed or daemon error"
                )

            with (
                self.subTest(code=code),
                patch.object(runtime, "verify_prepared"),
                patch.object(launcher, "bounded", fake),
            ):
                with self.assertRaisesRegex(RuntimeError, "absence not established"):
                    launcher.execute("/prepared")
            self.assertEqual(calls[-2][:2], ["docker", "rm"])
            self.assertEqual(calls[-1][:4], ["docker", "ps", "-a", "--filter"])
            container = calls[1][calls[1].index("--name") + 1]
            self.assertEqual(calls[-1][4], "name=^/" + container + "$")

    def test_provider_entry_observed_even_when_original_raises(self) -> None:
        observed: list[Any] = []
        entered: list[Any] = []

        def failed(instance: Any, request: Any) -> Any:
            entered.append(request)
            raise RuntimeError("original failed")

        with self.assertRaisesRegex(RuntimeError, "original failed"):
            harness.observe_completion(failed, observed, object(), "synthetic-request")
        self.assertEqual(entered, ["synthetic-request"])
        self.assertEqual(observed, [("synthetic-request", None)])

    def test_command_cleanup_reaps_even_when_kill_raises(self) -> None:
        class Child:
            stdout = None
            stderr = None
            waited = False

            def poll(self) -> None:
                return None

            def kill(self) -> None:
                raise OSError("synthetic kill failure")

            def wait(self, *, timeout: int) -> int:
                self.waited = True
                return 0

        child = Child()
        with patch.object(subprocess, "Popen", return_value=child):
            with self.assertRaisesRegex(OSError, "kill failure"):
                launcher.bounded(["synthetic"], 1)
        self.assertTrue(child.waited)

    def test_bounded_command_output_and_deadline(self) -> None:
        result = launcher.bounded([sys.executable, "-c", "print('ok')"], 5)
        self.assertEqual(result.stdout, "ok\n")
        with self.assertRaisesRegex(ValueError, "output bound"):
            launcher.bounded([sys.executable, "-c", "print('x'*2097152)"], 5)
        with self.assertRaises(TimeoutError):
            launcher.bounded([sys.executable, "-c", "import time; time.sleep(2)"], 0)

    def test_oracle_rejects_missing_reflection_extra_effects_and_lost_charge(
        self,
    ) -> None:
        report = {
            "status": "compiled",
            "reason": None,
            "compile_entries": 1,
            "reflection_predict_entries": 1,
            "stages": harness.STAGES,
            "new_dispatches": 5,
            "network_attempts": 0,
            "train_only": True,
            "observations_match_receipts": True,
            "selected_changed_instruction": True,
            "reflection_output_matches_selected": True,
            "usage": {
                "calls": 5,
                "tokens": 5,
                "cost_microunits": 5,
                "unresolved_calls": 0,
                "reason": None,
            },
            "initial_task_failures": 2,
            "changed_task_successes": 2,
        }
        harness.validate(report)
        for key, bad in (
            ("reflection_predict_entries", 0),
            ("new_dispatches", 6),
            ("train_only", False),
            ("selected_changed_instruction", False),
            ("observations_match_receipts", False),
        ):
            changed = {**report, key: bad}
            with self.subTest(key=key), self.assertRaises(ValueError):
                harness.validate(changed)
        changed = copy.deepcopy(report)
        changed["usage"]["calls"] = 4
        with self.assertRaisesRegex(ValueError, "accounting changed"):
            harness.validate(changed)


if __name__ == "__main__":
    unittest.main()
