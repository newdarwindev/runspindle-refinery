"""Bounded paper raster candidates and separately reviewed zero-tolerance checks.

No TeX compilation, baseline approval, network access or dependency installation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DESIGN = "docs/design/"
FIGURES = (
    "01-identity",
    "02-lifecycle",
    "03-approval",
    "04-golden-data",
    "05-control-planes",
    "06-constellation",
    "07-provenance",
    "08-observation",
)
SOURCES = {
    DESIGN + name
    for name in (
        "workflow-refinery.tex",
        "workflow-refinery.pdf",
        "build-evidence.json",
        "toolchain-lock.json",
        "artifacts.json",
        "source-disposition.json",
    )
} | {DESIGN + "figures/" + name + ".pdf" for name in FIGURES}
NAMES = tuple(f"page-{i}" for i in range(1, 9)) + tuple(
    "figure-" + name for name in FIGURES
)
TOOLS = {
    "renderer": "/usr/bin/pdftoppm",
    "text": "/usr/bin/pdftotext",
    "info": "/usr/bin/pdfinfo",
    "fonts": "/usr/bin/pdffonts",
    "sandbox": "/usr/bin/bwrap",
}
MAX_FILE = 32 * 1024 * 1024
MAX_OUTPUT = 80 * 1024 * 1024
SCALE = 1300


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path, limit: int = MAX_FILE) -> bytes:
    """Reject symlink components and special files before bounded reads."""
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise ValueError("noncanonical path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(absolute.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(absolute.parts[1:]) - 1:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
            raise ValueError("not a bounded regular file")
        data = b""
        while chunk := os.read(fd, min(1024 * 1024, limit + 1 - len(data))):
            data += chunk
            if len(data) > limit:
                raise ValueError("file size limit")
        return data
    except OSError as error:
        raise ValueError("unsafe or unreadable path") from error
    finally:
        os.close(fd)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load(path: Path) -> dict[str, Any]:
    value = json.loads(regular(path, 1024 * 1024), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    return value


def ppm(data: bytes) -> tuple[int, int, bytes]:
    # Poppler emits this canonical P6 header. Reject trailing/short data and
    # alternate encodings instead of silently normalizing malformed outputs.
    match = re.match(rb"P6\n([1-9][0-9]{0,3}) ([1-9][0-9]{0,3})\n255\n", data)
    if match is None:
        raise ValueError("noncanonical RGB PPM")
    width, height = map(int, match.groups())
    pixels = data[match.end() :]
    if max(width, height) > SCALE or len(pixels) != width * height * 3:
        raise ValueError("raster geometry or length")
    return width, height, pixels


def raster(data: bytes) -> dict[str, Any]:
    width, height, pixels = ppm(data)
    return {
        "width": width,
        "height": height,
        "mode": "RGB",
        "pixel_sha256": digest(pixels),
    }


def sha_map(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
        for k, v in value.items()
    )


def receipt_binding(receipt: Any) -> None:
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"path", "sha256"}
        or not isinstance(receipt["path"], str)
        or not receipt["path"].startswith("docs/paper-visual/")
        or ".." in Path(receipt["path"]).parts
        or not sha_map({"receipt": receipt["sha256"]})
    ):
        raise ValueError("invalid receipt binding")


def contract(manifest: dict[str, Any], *, reviewed: bool) -> None:
    if (
        manifest.get("schema") != "runspindle.paper-visual.v1"
        or manifest.get("scale") != SCALE
    ):
        raise ValueError("unsupported visual contract")
    if (
        manifest.get("pixel_tolerance") != 0
        or type(manifest.get("pixel_tolerance")) is not int
    ):
        raise ValueError("only zero pixel tolerance is supported")
    if manifest.get("tools") != TOOLS:
        raise ValueError("unexpected executable paths")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or not sha_map(sources) or set(sources) != SOURCES:
        raise ValueError("incomplete source closure")
    pins = manifest.get("runtime_files")
    if (
        not isinstance(pins, dict)
        or not sha_map(pins)
        or not set(TOOLS.values()).issubset(pins)
    ):
        raise ValueError("missing runtime pins")
    for path in pins:
        if ".." in Path(path).parts or not path.startswith(
            ("/usr/", "/lib/", "/lib64/", "/etc/fonts/", "/etc/os-release")
        ):
            raise ValueError("unsafe runtime pin")
    receipt_binding(manifest.get("source_build_receipt"))
    if manifest.get("status") not in {"candidate", "reviewed"}:
        raise ValueError("invalid baseline state")
    if reviewed and manifest["status"] != "reviewed":
        raise ValueError("baseline has not been reviewed")
    if reviewed:
        receipt_binding(manifest.get("review_receipt"))
    if set(manifest.get("text_anchors", {})) != set(NAMES):
        raise ValueError("all pages and figures require text expectations")
    for anchors in manifest["text_anchors"].values():
        if (
            not isinstance(anchors, list)
            or not anchors
            or any(not isinstance(a, str) or not a.strip() for a in anchors)
        ):
            raise ValueError("invalid ordered text anchors")
    links = manifest.get("links")
    if not isinstance(links, list) or not links:
        raise ValueError("missing actual link expectations")
    for link in links:
        if (
            not isinstance(link, dict)
            or set(link) != {"page", "url"}
            or type(link["page"]) is not int
            or not 1 <= link["page"] <= 8
            or not isinstance(link["url"], str)
            or not link["url"].startswith(
                "https://github.com/newdarwindev/runspindle-refinery/"
            )
        ):
            raise ValueError("invalid link expectation")
    if reviewed:
        refs = manifest.get("references", {})
        if set(refs) != set(NAMES):
            raise ValueError("missing raster references")
        for reference in refs.values():
            if (
                not isinstance(reference, dict)
                or set(reference) != {"width", "height", "mode", "pixel_sha256"}
                or reference["mode"] != "RGB"
                or not sha_map({"pixels": reference["pixel_sha256"]})
                or any(
                    type(reference[k]) is not int or not 1 <= reference[k] <= SCALE
                    for k in ("width", "height")
                )
            ):
                raise ValueError("invalid raster reference")


def verify_inputs(root: Path, manifest: dict[str, Any]) -> None:
    receipt = manifest["source_build_receipt"]
    if digest(regular(root / receipt["path"])) != receipt["sha256"]:
        raise ValueError("source build receipt mismatch")
    for name, expected in manifest["sources"].items():
        if digest(regular(root / name)) != expected:
            raise ValueError("source mismatch: " + name)
    for name, expected in manifest["runtime_files"].items():
        # System library symlinks are resolved to their pinned target. Input and
        # output artifacts, unlike trusted system packages, never follow links.
        if digest(regular(Path(name).resolve())) != expected:
            raise ValueError("runtime mismatch: " + name)


def ordered(text: str, anchors: list[str]) -> None:
    normalized = " ".join(text.split())
    start = 0
    for anchor in anchors:
        at = normalized.find(" ".join(anchor.split()), start)
        if at < 0:
            raise ValueError("missing or reordered text anchor: " + anchor)
        start = at + len(" ".join(anchor.split()))


def links(data: bytes) -> list[dict[str, Any]]:
    lines = data.decode("utf-8").splitlines()
    if not lines or lines[0].split() != ["Page", "Type", "URL"]:
        raise ValueError("unrecognized PDF link inventory")
    result = []
    for line in lines[1:]:
        match = re.fullmatch(r"\s*([1-8])\s+Annotation\s+(https://\S+)\s*", line)
        if match is None:
            raise ValueError("unexpected PDF link")
        result.append({"page": int(match[1]), "url": match[2]})
    return result


def output_names() -> set[str]:
    return {name + suffix for name in NAMES for suffix in (".ppm", ".txt")} | {
        "links.txt",
        "geometry.txt",
        "fonts.txt",
    }


def inspect(output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if output.is_symlink() or {p.name for p in output.iterdir()} != output_names():
        raise ValueError("missing or unexpected output")
    data = {name: regular(output / name) for name in sorted(output_names())}
    if sum(map(len, data.values())) > MAX_OUTPUT:
        raise ValueError("total output limit")
    geometry = data["geometry.txt"].decode("utf-8")
    if not re.search(r"^Pages:\s+8\s*$", geometry, re.M) or not re.search(
        r"^Page size:\s+612 x 792 pts \(letter\)\s*$", geometry, re.M
    ):
        raise ValueError("paper must have eight native Letter pages")
    font_lines = data["fonts.txt"].decode("utf-8").splitlines()
    if len(font_lines) < 3 or "emb sub uni" not in font_lines[0]:
        raise ValueError("unrecognized font inventory")
    if any(line.split()[-5:-2] != ["yes", "yes", "yes"] for line in font_lines[2:]):
        raise ValueError("fonts must be embedded, subset and Unicode mapped")
    for name in NAMES:
        ordered(data[name + ".txt"].decode("utf-8"), manifest["text_anchors"][name])
    if links(data["links.txt"]) != manifest["links"]:
        raise ValueError("actual PDF links differ")
    return {name: raster(data[name + ".ppm"]) for name in NAMES}


def compare(actual: dict[str, Any], reference: dict[str, Any]) -> None:
    if set(actual) != set(NAMES) or set(reference) != set(NAMES):
        raise ValueError("incomplete raster comparison")
    different = [name for name in NAMES if actual[name] != reference[name]]
    if different:
        raise ValueError("zero-tolerance raster mismatch: " + ", ".join(different))


def check(root: Path, output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    contract(manifest, reviewed=True)
    receipt = manifest["review_receipt"]
    if digest(regular(root / receipt["path"])) != receipt["sha256"]:
        raise ValueError("review receipt mismatch")
    verify_inputs(root, manifest)
    actual = inspect(output, manifest)
    compare(actual, manifest["references"])
    return {
        "status": "passed",
        "evidence_class": "retained_source_built_pdf_visual_comparison",
        "pages": 8,
        "figures": 8,
        "pixel_tolerance": 0,
        "this_command_tex_build": "not_run",
        "source_build_receipt": manifest["source_build_receipt"],
        "runtime_acceptance": "not_run",
        "actual": actual,
    }


def render(root: Path, output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    contract(manifest, reviewed=False)
    verify_inputs(root, manifest)
    output = output.absolute()
    if output.exists() or output.is_symlink() or ".." in output.parts:
        raise ValueError("choose a fresh output directory")
    # Reject parent symlinks; existing canonical directory required, no mkdir -p.
    if output.parent.resolve() != output.parent or not output.parent.is_dir():
        raise ValueError("unsafe output parent")
    output.mkdir(mode=0o700)
    base = [
        TOOLS["sandbox"],
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/etc/fonts",
        "/etc/fonts",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(root.resolve() / "docs/design"),
        "/src",
        "--bind",
        str(output),
        "/out",
        "--chdir",
        "/src",
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "1794614400",
    }

    def run(argv: list[str], stdout: str | None = None) -> None:
        def limits() -> None:
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE, MAX_FILE))
            resource.setrlimit(
                resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024)
            )
            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))

        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            result = subprocess.run(
                base + argv,
                env=env,
                stdout=out,
                stderr=err,
                timeout=30,
                check=False,
                preexec_fn=limits,
            )
            out.seek(0)
            err.seek(0)
            captured = out.read(1024 * 1024 + 1)
            errors = err.read(1024 * 1024 + 1)
        if result.returncode or len(captured) + len(errors) > 1024 * 1024:
            raise ValueError("isolated renderer failed; partial output retained")
        if stdout:
            with (output / stdout).open("xb") as stream:
                stream.write(captured)
        if sum(p.stat().st_size for p in output.iterdir()) > MAX_OUTPUT:
            raise ValueError("total output limit")

    for index, name in enumerate(NAMES):
        page = index + 1 if index < 8 else 1
        pdf = (
            "/src/workflow-refinery.pdf"
            if index < 8
            else "/src/figures/" + FIGURES[index - 8] + ".pdf"
        )
        run(
            [
                TOOLS["renderer"],
                "-f",
                str(page),
                "-l",
                str(page),
                "-scale-to",
                str(SCALE),
                "-singlefile",
                pdf,
                "/out/" + name,
            ]
        )
        run(
            [
                TOOLS["text"],
                "-f",
                str(page),
                "-l",
                str(page),
                "-layout",
                "-enc",
                "UTF-8",
                pdf,
                "/out/" + name + ".txt",
            ]
        )
    run([TOOLS["info"], "-url", "/src/workflow-refinery.pdf"], "links.txt")
    run([TOOLS["info"], "/src/workflow-refinery.pdf"], "geometry.txt")
    run([TOOLS["fonts"], "/src/workflow-refinery.pdf"], "fonts.txt")
    actual = inspect(output, manifest)
    return {
        "status": "candidate_not_reviewed",
        "this_command_tex_build": "not_run",
        "source_build_receipt": manifest["source_build_receipt"],
        "runtime_acceptance": "not_run",
        "actual": actual,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "check"))
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = load(args.manifest)
    result = (
        render(ROOT, args.output, manifest)
        if args.action == "render"
        else check(ROOT, args.output, manifest)
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
