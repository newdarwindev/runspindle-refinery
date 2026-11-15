"""Synthetic dependency inventories only; never import DSPy or invoke Docker."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / "tools/offline_dspy.py"
spec = importlib.util.spec_from_file_location("offline_inventory", TOOL)
if spec is None or spec.loader is None:
    raise RuntimeError("missing inventory module")
engine: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class InventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        for index in range(76):
            name = "dspy" if index == 0 else "synthetic" + str(index)
            version = "3.1.0" if index == 0 else "1.0"
            dist = self.prepared / (name + ".dist-info")
            dist.mkdir()
            (dist / "METADATA").write_text(
                f"Name: {name}\nVersion: {version}\nLicense: MIT\n"
            )
            (dist / "LICENSE").write_text("Synthetic notice")
        (self.prepared / "synthetic.py").write_text("VALUE = 'reviewed'\n")
        offline = self.root / "offline"
        offline.mkdir()
        (offline / "requirements.lock").write_text("synthetic lock")
        self.root_patch = patch.object(engine, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.original = engine.inventory(self.prepared)
        (offline / "dependencies.json").write_text(json.dumps(self.original))
        (offline / "toolchain.json").write_text(
            json.dumps({"image": engine.IMAGE, "target": "linux/arm64", "notices": {}})
        )

    def test_clean_inventory_unchanged(self) -> None:
        self.assertEqual(engine.inventory(self.prepared), self.original)
        self.assertEqual(len(self.original["dependencies"]), 76)
        self.assertIn("synthetic.py", self.original["installed_files"])

    def test_bytecode_rejected_before_any_container_call(self) -> None:
        for relative in (
            "__pycache__/synthetic.cpython-314.pyc",
            "package/__pycache__/nested.cpython-314.opt-1.pyc",
            "standalone.pyc",
        ):
            with self.subTest(relative=relative):
                path = self.prepared / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"inert synthetic bytecode marker, never executed")
                with patch.object(
                    engine.subprocess,
                    "run",
                    side_effect=AssertionError("unexpected subprocess effect"),
                ) as calls:
                    with self.assertRaisesRegex(ValueError, "bytecode"):
                        engine.execute(str(self.prepared))
                    calls.assert_not_called()
                path.unlink()
                while path.parent != self.prepared:
                    path = path.parent
                    path.rmdir()

    def test_empty_cache_directory_rejected(self) -> None:
        (self.prepared / "__pycache__").mkdir()
        with self.assertRaisesRegex(ValueError, "bytecode"):
            engine.inventory(self.prepared)

    def test_changed_cached_bytes_never_disappear_from_admission(self) -> None:
        cache = self.prepared / "__pycache__"
        cache.mkdir()
        path = cache / "synthetic.cpython-314.pyc"
        for data in (b"first inert marker", b"changed inert marker"):
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "bytecode"):
                engine.inventory(self.prepared)

    def test_guard_removal_mutation_reproduces_false_inventory_match(self) -> None:
        cache = self.prepared / "__pycache__"
        cache.mkdir()
        (cache / "synthetic.cpython-314.pyc").write_bytes(b"inert marker")
        with self.assertRaisesRegex(ValueError, "bytecode"):
            engine.inventory(self.prepared)
        with patch.object(engine, "reject_bytecode", return_value=None):
            self.assertEqual(engine.inventory(self.prepared), self.original)
            with self.assertRaises(AssertionError):
                with self.assertRaisesRegex(ValueError, "bytecode"):
                    engine.inventory(self.prepared)


if __name__ == "__main__":
    unittest.main()
