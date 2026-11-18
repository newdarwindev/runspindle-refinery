"""Synthetic launcher effects only; never import DSPy or invoke Docker."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import offline_dspy as engine

ROOT = Path(__file__).resolve().parents[1]


class DependencyAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in engine.ADOPTION_FILES | {"offline/dependency-adoption.json"}:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        self.declared = json.loads(
            (self.root / "offline/dependencies.json").read_bytes()
        )
        self.root_patch = patch.object(engine, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def deny_without_effects(self) -> None:
        with (
            patch.object(
                engine, "inventory", return_value=copy.deepcopy(self.declared)
            ),
            patch.object(subprocess, "run") as effect,
        ):
            with self.assertRaises(ValueError):
                engine.execute(str(self.root / "synthetic-prepared"), "optimizer")
            effect.assert_not_called()

    def test_reviewed_closure_matches_exact_candidate(self) -> None:
        declared, toolchain = engine.reviewed_closure()
        self.assertEqual(declared, self.declared)
        self.assertEqual(toolchain["image"], engine.IMAGE)
        historical = (
            self.root
            / "offline/historical/dependencies-before-linux-native-20261118.json"
        )
        self.assertEqual(
            hashlib.sha256(historical.read_bytes()).hexdigest(),
            "087981ca63d9ca13d8a9a1a33072f553885a52c089b0b419dd31c08c6b27de52",
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "offline/dependencies.json").read_bytes()
            ).hexdigest(),
            "84477d54127b86bc47b5d2323fd40715ec1b67d4a260e174ad9c60161a313a8c",
        )

    def test_every_missing_or_changed_source_denies_before_docker(self) -> None:
        for name in sorted(
            engine.ADOPTION_FILES | {"offline/dependency-adoption.json"}
        ):
            path = self.root / name
            original = path.read_bytes()
            with self.subTest(name=name, mutation="missing"):
                path.unlink()
                self.deny_without_effects()
            with self.subTest(name=name, mutation="changed"):
                path.write_bytes(original + b" ")
                self.deny_without_effects()
            path.write_bytes(original)

    def test_altered_receipt_cannot_self_authorize_replacement(self) -> None:
        path = self.root / "offline/dependencies.json"
        path.write_bytes(b'{"dspy":"3.1.0"}')
        receipt = self.root / "offline/dependency-adoption.json"
        value = json.loads(receipt.read_bytes())
        value["files"]["offline/dependencies.json"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        receipt.write_text(json.dumps(value))
        self.deny_without_effects()

    def test_removed_fixed_receipt_pin_exposes_self_authorization(self) -> None:
        recipe = self.root / "offline/linux-native-candidate/recipe.json"
        recipe.write_bytes(recipe.read_bytes() + b" ")
        receipt = self.root / "offline/dependency-adoption.json"
        value = json.loads(receipt.read_bytes())
        value["files"]["offline/linux-native-candidate/recipe.json"] = hashlib.sha256(
            recipe.read_bytes()
        ).hexdigest()
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            engine.reviewed_closure()
        # Deliberately substitute an attacker-updated pin: the independent
        # denial expectation now fails, proving the fixed source pin matters.
        with patch.object(
            engine, "ADOPTION_SHA256", hashlib.sha256(receipt.read_bytes()).hexdigest()
        ):
            with self.assertRaises(AssertionError):
                with self.assertRaises(ValueError):
                    engine.reviewed_closure()

    def test_changed_image_constant_or_inventory_denied(self) -> None:
        with patch.object(engine, "IMAGE", "unreviewed-image"):
            self.deny_without_effects()
        self.declared["installed_files"]["injected.py"] = "0" * 64
        self.deny_without_effects()

    def test_symlinked_receipt_and_source_denied(self) -> None:
        for name in ("offline/dependency-adoption.json", "offline/toolchain.json"):
            path = self.root / name
            original = path.read_bytes()
            external = self.root / "alias-target"
            external.write_bytes(original)
            path.unlink()
            path.symlink_to(external)
            with self.subTest(name=name):
                self.deny_without_effects()
            path.unlink()
            path.write_bytes(original)

    def test_valid_gate_preserves_bounded_dispatch_and_cleanup(self) -> None:
        replies = [
            subprocess.CompletedProcess([], 0, '{"synthetic_test":true}', ""),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 1),
        ]
        with (
            patch.object(engine, "inventory", return_value=self.declared),
            patch.object(subprocess, "run", side_effect=replies) as effect,
        ):
            self.assertEqual(
                engine.execute(str(self.root / "synthetic-prepared"), "optimizer"),
                {"synthetic_test": True},
            )
        argv = effect.call_args_list[0].args[0]
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--pull") + 1], "never")
        self.assertIn("--read-only", argv)
        self.assertIn(engine.IMAGE, argv)
        self.assertEqual(argv[-1], "tools/test_dspy_optimizer.py")
        self.assertEqual(effect.call_count, 3)
        self.assertEqual(
            effect.call_args_list[1].args[0][:3], ["docker", "rm", "--force"]
        )
        self.assertEqual(effect.call_args_list[2].args[0][:2], ["docker", "inspect"])

    def test_historical_closure_cannot_silently_resume(self) -> None:
        self.declared = json.loads(
            (
                self.root
                / "offline/historical/dependencies-before-linux-native-20261118.json"
            ).read_bytes()
        )
        self.deny_without_effects()
