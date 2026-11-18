"""Bounded missing-label probe of actual rasters, never of the source PDF."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools import paper_visual as visual


def probe(actual: Path, manifest: dict[str, Any], output: Path) -> dict[str, Any]:
    visual.check(visual.ROOT, actual, manifest)
    output = output.absolute()
    if (
        output.exists()
        or output.is_symlink()
        or output.parent.resolve() != output.parent
        or not output.parent.is_dir()
    ):
        raise ValueError("choose a fresh canonical output directory")
    name = "figure-03-approval"
    width, height, pixels = visual.ppm(visual.regular(actual / (name + ".ppm")))
    rectangle = (100, 30, 230, 75)
    if width < rectangle[2] or height < rectangle[3]:
        raise ValueError("unexpected actual figure geometry")
    changed = bytearray(pixels)
    for y in range(rectangle[1], rectangle[3]):
        for x in range(rectangle[0], rectangle[2]):
            at = (y * width + x) * 3
            changed[at : at + 3] = b"\xff\xff\xff"
    differences = [
        pixels[i : i + 3] != changed[i : i + 3] for i in range(0, len(pixels), 3)
    ]
    count = sum(differences)
    if count == 0:
        raise ValueError("probe must remove visible pixels")
    header = f"P6\n{width} {height}\n255\n".encode()
    clipped = header + bytes(changed)
    actual_rows = visual.inspect(actual, manifest)
    actual_rows[name] = visual.raster(clipped)
    try:
        visual.compare(actual_rows, manifest["references"])
    except ValueError as error:
        if str(error) != "zero-tolerance raster mismatch: " + name:
            raise
    else:
        raise ValueError("broken comparator accepted the clipped label")
    output.mkdir(mode=0o700)
    (output / "clipped.ppm").write_bytes(clipped)
    diff = header + b"".join(
        b"\xff\x00\x00" if different else b"\xff\xff\xff" for different in differences
    )
    (output / "diff.ppm").write_bytes(diff)
    return {
        "status": "detected",
        "evidence_class": "actual_raster_label_clipping_not_source_mutation",
        "raster": name,
        "rectangle_xyxy": rectangle,
        "changed_pixels": count,
        "original_pixel_sha256": visual.digest(pixels),
        "clipped_pixel_sha256": visual.digest(bytes(changed)),
        "diff_pixel_sha256": visual.raster(diff)["pixel_sha256"],
        "reference_modified": False,
        "source_pdf_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actual", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(probe(args.actual, visual.load(args.manifest), args.output)))


if __name__ == "__main__":
    main()
