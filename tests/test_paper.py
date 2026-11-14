"""Document closure regression checks, independent of TeX availability."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "paper_check", ROOT / "tools/check_paper.py"
)
assert SPEC and SPEC.loader
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PaperClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "docs/design", self.root / "docs/design")
        shutil.copyfile(ROOT / "provenance.json", self.root / "provenance.json")

    def test_valid_frozen_paper(self) -> None:
        self.assertEqual(CHECK.validate(self.root)["pages"], 8)

    def test_reject_changed_source(self) -> None:
        with (self.root / "docs/design/workflow-refinery.tex").open("a") as f:
            f.write("tampered")
        with self.assertRaises(AssertionError):
            CHECK.validate(self.root)

    def test_reject_changed_pdf(self) -> None:
        (self.root / "docs/design/workflow-refinery.pdf").write_bytes(
            b"not the accepted build"
        )
        with self.assertRaises(AssertionError):
            CHECK.validate(self.root)

    def test_reject_missing_figure(self) -> None:
        next((self.root / "docs/design/figures").glob("*.pdf")).unlink()
        with self.assertRaises(AssertionError):
            CHECK.validate(self.root)

    def test_reject_unlisted_asset(self) -> None:
        (self.root / "docs/design/unreviewed.txt").write_text("new unreviewed input")
        with self.assertRaises(AssertionError):
            CHECK.validate(self.root)

    def test_reject_stale_scope(self) -> None:
        p = self.root / "provenance.json"
        data = json.loads(p.read_text())
        next(s for s in data["sources"] if s["source_id"] == "SRC-013")[
            "migration_status"
        ] = "partial"
        p.write_text(json.dumps(data))
        with self.assertRaises(AssertionError):
            CHECK.validate(self.root)


if __name__ == "__main__":
    unittest.main()
