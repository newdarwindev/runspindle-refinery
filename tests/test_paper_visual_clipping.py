"""Synthetic probe negatives and retained visual-artifact integrity."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import paper_visual as visual
from tools import paper_visual_clipping as clipping


class ClippingProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.actual = self.root / "actual"
        self.actual.mkdir()
        self.output = self.root / "probe"
        self.image = b"P6\n300 100\n255\n" + b"\x00" * (300 * 100 * 3)
        self.path = self.actual / "figure-03-approval.ppm"
        self.path.write_bytes(self.image)
        self.rows = {name: visual.raster(self.image) for name in visual.NAMES}
        self.manifest = {"references": copy.deepcopy(self.rows)}

    def test_probe_detects_change_preserves_source_and_references(self) -> None:
        original = copy.deepcopy(self.manifest)
        with (
            patch.object(visual, "check"),
            patch.object(visual, "inspect", return_value=copy.deepcopy(self.rows)),
        ):
            result = clipping.probe(self.actual, self.manifest, self.output)
        self.assertEqual(result["status"], "detected")
        self.assertEqual(result["changed_pixels"], 130 * 45)
        self.assertEqual(self.path.read_bytes(), self.image)
        self.assertEqual(self.manifest, original)
        self.assertEqual(
            visual.raster((self.output / "diff.ppm").read_bytes())["pixel_sha256"],
            result["diff_pixel_sha256"],
        )

    def test_broken_comparator_cannot_report_success(self) -> None:
        with (
            patch.object(visual, "check"),
            patch.object(visual, "inspect", return_value=copy.deepcopy(self.rows)),
            patch.object(visual, "compare"),
            self.assertRaisesRegex(ValueError, "broken comparator"),
        ):
            clipping.probe(self.actual, self.manifest, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_output_refused_without_modification(self) -> None:
        self.output.mkdir()
        marker = self.output / "retained"
        marker.write_bytes(b"keep")
        with (
            patch.object(visual, "check"),
            self.assertRaisesRegex(ValueError, "fresh canonical"),
        ):
            clipping.probe(self.actual, self.manifest, self.output)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_blank_region_is_not_a_successful_mutation(self) -> None:
        self.path.write_bytes(b"P6\n300 100\n255\n" + b"\xff" * (300 * 100 * 3))
        with (
            patch.object(visual, "check"),
            self.assertRaisesRegex(ValueError, "visible pixels"),
        ):
            clipping.probe(self.actual, self.manifest, self.output)
        self.assertFalse(self.output.exists())


class CommittedVisualArtifactsTests(unittest.TestCase):
    def test_complete_receipt_and_preview_linkage(self) -> None:
        root = Path(__file__).resolve().parents[1]
        folder = root / "docs/paper-visual"
        manifest = visual.load(folder / "manifest.json")
        review = visual.load(folder / "review.json")
        evidence = visual.load(folder / "evidence.json")
        visual.contract(manifest, reviewed=True)
        for field, filename in (
            ("manifest_sha256", "manifest.json"),
            ("review_receipt_sha256", "review.json"),
            ("source_build_receipt_sha256", "build-receipt.json"),
        ):
            self.assertEqual(
                visual.digest(visual.regular(folder / filename)), evidence[field]
            )
        for key in ("review_receipt", "source_build_receipt"):
            receipt = manifest[key]
            self.assertEqual(
                visual.digest(visual.regular(root / receipt["path"])), receipt["sha256"]
            )
        for path, expected in manifest["sources"].items():
            self.assertEqual(visual.digest(visual.regular(root / path)), expected)
        previews = review["preview_sha256"]
        self.assertEqual(
            set(previews),
            {"references/" + name + ".png" for name in visual.NAMES}
            | {"references/page-contact.png", "references/figure-contact.png"},
        )
        for path, expected in previews.items():
            self.assertEqual(
                visual.digest(visual.regular(folder / path)), expected, path
            )
        for path, expected in evidence["clipping_previews"].items():
            self.assertEqual(
                visual.digest(visual.regular(folder / "mutation" / path)), expected
            )
        self.assertEqual(
            visual.digest(visual.regular(root / "tools/paper_visual_clipping.py")),
            evidence["clipping_tool_sha256"],
        )
        rows = evidence["rasters"]
        self.assertEqual(len(rows), 16)
        self.assertEqual({row["name"] for row in rows}, set(visual.NAMES))
        for row in rows:
            self.assertEqual(row["reference"], manifest["references"][row["name"]])
            self.assertEqual(row["actual_render_1"], row["reference"])
            self.assertEqual(row["actual_render_2"], row["reference"])
            self.assertEqual(row["different_pixels"], 0)
        self.assertEqual([page["page"] for page in review["pages"]], list(range(1, 9)))
        self.assertEqual(evidence["runtime_acceptance"], "not_run")
