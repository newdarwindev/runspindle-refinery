"""Keep the merged paper check mandatory and assigned exactly once."""

from __future__ import annotations

import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools import partition_validation as gate

ROOT = Path(__file__).resolve().parents[1]


class PaperHygieneTests(unittest.TestCase):
    def test_paper_check_has_one_explicit_dispatch(self) -> None:
        partitions, inventory, _, _ = gate.manifests(ROOT)
        entries = [
            entry for entry in inventory["checks"] if entry["id"] == "check-paper"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["command"], ["python3", "tools/check_paper.py"])
        self.assertNotIn("not_applicable", entries[0])
        self.assertIn("docs/design/workflow-refinery.pdf", entries[0]["required_files"])
        self.assertEqual(partitions["partitions"]["component"].count("check-paper"), 1)
        self.assertEqual(
            sum(ids.count("check-paper") for ids in partitions["partitions"].values()),
            1,
        )
        scaffold = ast.parse((ROOT / "tools/check_scaffold.py").read_text())
        self.assertFalse(
            any(
                isinstance(node, ast.Constant) and node.value == "tools/check_paper.py"
                for node in ast.walk(scaffold)
            )
        )

    def test_missing_or_duplicate_paper_assignment_fails_closed(self) -> None:
        for change in ("missing", "duplicate"):
            with (
                self.subTest(change=change),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                shutil.copyfile(ROOT / "hygiene.json", root / "hygiene.json")
                data = json.loads((ROOT / "validation-partitions.json").read_text())
                if change == "missing":
                    data["partitions"]["component"].remove("check-paper")
                else:
                    data["partitions"]["quality"].append("check-paper")
                (root / "validation-partitions.json").write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "missing or duplicate"):
                    gate.manifests(root)


if __name__ == "__main__":
    unittest.main()
