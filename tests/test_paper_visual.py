"""Synthetic visual-contract tests, not a paper render or baseline review."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import paper_visual as visual


class PaperVisualTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "actual"
        self.output.mkdir()
        # An invented black label on white. This is never a paper baseline.
        self.pixels = b"\xff\xff\xff" * 3 + b"\x00\x00\x00"
        self.image = b"P6\n2 2\n255\n" + self.pixels
        url = (
            "https://github.com/newdarwindev/runspindle-refinery/tree/main/docs/design"
        )
        self.manifest: dict[str, Any] = {
            "schema": "runspindle.paper-visual.v1",
            "scale": 1300,
            "pixel_tolerance": 0,
            "tools": visual.TOOLS.copy(),
            "sources": {name: "0" * 64 for name in visual.SOURCES},
            "runtime_files": {name: "0" * 64 for name in visual.TOOLS.values()},
            "source_build_receipt": {
                "path": "docs/paper-visual/source-build.json",
                "sha256": visual.digest(b"synthetic"),
            },
            "status": "reviewed",
            "review_receipt": {
                "path": "docs/paper-visual/review.json",
                "sha256": visual.digest(b"reviewed synthetic"),
            },
            "text_anchors": {name: ["first", "label", "last"] for name in visual.NAMES},
            "links": [{"page": 8, "url": url}],
            "references": {name: visual.raster(self.image) for name in visual.NAMES},
        }
        receipt = self.root / "docs/paper-visual/source-build.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_bytes(b"synthetic")
        (receipt.parent / "review.json").write_bytes(b"reviewed synthetic")
        (self.output / "fonts.txt").write_text(
            "name type encoding emb sub uni object ID\n---\nSynthetic Type1 Custom yes yes yes 1 0\n"
        )
        for name in visual.NAMES:
            (self.output / (name + ".ppm")).write_bytes(self.image)
            (self.output / (name + ".txt")).write_text("first\nlabel last\f")
        (self.output / "links.txt").write_text(
            "Page  Type          URL\n 8 Annotation " + url + "\n"
        )
        (self.output / "geometry.txt").write_text(
            "Pages: 8\nPage size: 612 x 792 pts (letter)\n"
        )

    def test_complete_synthetic_visual_contract(self) -> None:
        visual.contract(self.manifest, reviewed=True)
        actual = visual.inspect(self.output, self.manifest)
        visual.compare(actual, self.manifest["references"])
        self.assertEqual(len(actual), 16)
        self.assertEqual(actual["page-1"]["pixel_sha256"], visual.digest(self.pixels))

    def test_clipped_label_is_detected_without_updating_reference(self) -> None:
        original = copy.deepcopy(self.manifest)
        target = self.output / "page-4.ppm"
        target.write_bytes(b"P6\n2 2\n255\n" + b"\xff" * 12)
        actual = visual.inspect(self.output, self.manifest)
        self.assertNotEqual(actual["page-4"], self.manifest["references"]["page-4"])
        with self.assertRaisesRegex(ValueError, "page-4"):
            visual.compare(actual, self.manifest["references"])
        self.assertEqual(self.manifest, original)

    def test_every_page_and_figure_is_required(self) -> None:
        for name in visual.NAMES:
            with self.subTest(name=name):
                path = self.output / (name + ".ppm")
                path.unlink()
                with self.assertRaisesRegex(ValueError, "missing"):
                    visual.inspect(self.output, self.manifest)
                path.write_bytes(self.image)

    def test_every_reference_is_required(self) -> None:
        for name in visual.NAMES:
            changed = copy.deepcopy(self.manifest)
            del changed["references"][name]
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "references"),
            ):
                visual.contract(changed, reviewed=True)

    def test_extra_output_rejected(self) -> None:
        (self.output / "extra").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            visual.inspect(self.output, self.manifest)

    def test_bad_ppm_geometry_length_and_modes(self) -> None:
        for data in (
            b"P3\n2 2\n255\n",
            self.image + b"x",
            self.image[:-1],
            b"P6\n1301 1\n255\n",
            b"P6\n2 2\n256\n" + self.pixels,
        ):
            with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                visual.ppm(data)

    def test_missing_reordered_or_changed_caption(self) -> None:
        for text in ("first last", "last label first", "first changed last"):
            (self.output / "page-4.txt").write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "anchor"):
                visual.inspect(self.output, self.manifest)

    def test_changed_links_fail_even_when_pixels_match(self) -> None:
        (self.output / "links.txt").write_text(
            "Page Type URL\n 8 Annotation https://example.com/\n"
        )
        with self.assertRaisesRegex(ValueError, "links differ"):
            visual.inspect(self.output, self.manifest)

    def test_geometry_mismatch(self) -> None:
        for text in (
            "Pages: 7\nPage size: 612 x 792 pts (letter)\n",
            "Pages: 8\nPage size: 595 x 842 pts (A4)\n",
        ):
            (self.output / "geometry.txt").write_text(text)
            with self.assertRaisesRegex(ValueError, "Letter"):
                visual.inspect(self.output, self.manifest)

    def test_candidate_is_not_acceptance(self) -> None:
        self.manifest["status"] = "candidate"
        visual.contract(self.manifest, reviewed=False)
        with self.assertRaisesRegex(ValueError, "not been reviewed"):
            visual.contract(self.manifest, reviewed=True)

    def test_missing_pins_or_nonzero_tolerance_rejected(self) -> None:
        for key, value in (
            ("runtime_files", {}),
            ("sources", {}),
            ("pixel_tolerance", 1),
            ("pixel_tolerance", False),
            ("review_receipt", ""),
            ("tools", {}),
        ):
            changed = dict(self.manifest, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                visual.contract(changed, reviewed=True)

    def test_input_hashes_must_match(self) -> None:
        for name in visual.SOURCES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"actual")
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            visual.verify_inputs(self.root, self.manifest)

    def test_symlinks_and_size_limits_rejected(self) -> None:
        path = self.output / "page-1.ppm"
        path.unlink()
        path.symlink_to(self.output / "page-2.ppm")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            visual.inspect(self.output, self.manifest)
        with self.assertRaisesRegex(ValueError, "bounded"):
            visual.regular(self.output / "page-2.ppm", 1)

    def test_review_receipt_binding_is_strict(self) -> None:
        for value in (
            None,
            True,
            "reviewed",
            {},
            {"path": "../outside", "sha256": "0" * 64},
            {"path": "docs/paper-visual/review.json", "sha256": "bad"},
        ):
            self.manifest["review_receipt"] = value
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "receipt binding"),
            ):
                visual.contract(self.manifest, reviewed=True)

    def test_changed_or_missing_review_receipt_fails_check(self) -> None:
        receipt = self.root / "docs/paper-visual/review.json"
        receipt.write_bytes(b"changed")
        with (
            patch.object(visual, "verify_inputs"),
            self.assertRaisesRegex(ValueError, "review receipt mismatch"),
        ):
            visual.check(self.root, self.output, self.manifest)
        receipt.unlink()
        with (
            patch.object(visual, "verify_inputs"),
            self.assertRaisesRegex(ValueError, "unsafe or unreadable"),
        ):
            visual.check(self.root, self.output, self.manifest)

    def test_candidate_contract_requires_no_review_receipt(self) -> None:
        self.manifest["status"] = "candidate"
        del self.manifest["review_receipt"]
        visual.contract(self.manifest, reviewed=False)

    def test_runtime_hash_mismatch_rejected(self) -> None:
        with patch.object(visual, "regular", return_value=b"synthetic"):
            manifest = copy.deepcopy(self.manifest)
            manifest["sources"] = {
                name: visual.digest(b"synthetic") for name in visual.SOURCES
            }
            with self.assertRaisesRegex(ValueError, "runtime mismatch"):
                visual.verify_inputs(self.root, manifest)

    def test_missing_source_build_receipt_rejected(self) -> None:
        del self.manifest["source_build_receipt"]
        with self.assertRaisesRegex(ValueError, "receipt binding"):
            visual.contract(self.manifest, reviewed=True)

    def test_nonembedded_font_rejected(self) -> None:
        (self.output / "fonts.txt").write_text(
            "name type encoding emb sub uni object ID\n---\nSynthetic Type1 Custom no yes yes 1 0\n"
        )
        with self.assertRaisesRegex(ValueError, "embedded"):
            visual.inspect(self.output, self.manifest)

    def test_render_never_overwrites_output(self) -> None:
        with (
            patch.object(visual, "verify_inputs"),
            self.assertRaisesRegex(ValueError, "fresh output"),
        ):
            visual.render(self.root, self.output, self.manifest)
        self.assertEqual((self.output / "page-1.ppm").read_bytes(), self.image)

    def test_duplicate_json_keys_rejected(self) -> None:
        path = self.root / "manifest.json"
        path.write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            visual.load(path)

    def test_check_does_not_write_manifest_or_outputs(self) -> None:
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        manifest_before = json.dumps(self.manifest, sort_keys=True)
        with patch.object(visual, "verify_inputs"):
            result = visual.check(self.root, self.output, self.manifest)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(manifest_before, json.dumps(self.manifest, sort_keys=True))
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in self.output.iterdir()}
        )
