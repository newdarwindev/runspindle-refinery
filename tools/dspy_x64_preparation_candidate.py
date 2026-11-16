#!/usr/bin/env python3
"""Read-only Linux x86_64 preparation comparison; never execution authority."""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import re
import stat
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from tools import dspy_preparation_candidate as common

ROOT = Path(__file__).resolve().parents[1]
LOCK_SHA256 = "830486665f562043c93297ff9da93db9b379c13cf12eba680b320dc1c5b4b525"
UV_URL = "https://github.com/astral-sh/uv/releases/download/0.10.6/uv-x86_64-unknown-linux-gnu.tar.gz"
UV_SHA256 = "aaa402e19d14a6b9a4267fcf4ec35380f804c68923525cea67cd6ee05bb4e930"
UV_CHECKSUM_SHA256 = "524a2c0597ab3d4736b2fdaf325e3d0980a01eccb343e469e14e6de75b473b20"
PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.14.10%2B20261110-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_SHA256 = "3fe0d7c0538eb5d190c34582992d26230532551355cbd789fabc6591621cfc07"
MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 30000
# Entries contain kind, permission bits, and exact bytes/link text.
Entry = tuple[str, int, bytes]


def canonical(name: str) -> bool:
    return (
        bool(name)
        and not name.startswith("/")
        and all(part not in {"", ".", ".."} for part in name.split("/"))
        and name.count("/") <= 64
        and "\\" not in name
    )


def stamp(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def snapshot(path: Path, *, links: bool = False) -> dict[str, Entry]:
    """No-follow descriptor snapshot; reject changes during reads, not atomic FS proof."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open("/", flags | os.O_DIRECTORY)
    result: dict[str, Entry] = {}
    total = 0
    nodes = 0

    def walk(parent: int, prefix: str) -> None:
        nonlocal total, nodes
        before = os.fstat(parent)
        for name in sorted(os.listdir(parent)):
            relative = prefix + name
            nodes += 1
            if not canonical(relative) or nodes > MAX_FILES:
                raise ValueError("input path or node limit")
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                if not links:
                    raise ValueError("symlink input")
                data = os.readlink(name, dir_fd=parent).encode()
                if stamp(info) != stamp(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                ):
                    raise ValueError("input changed during snapshot")
                total += len(data)
                result[relative] = ("link", 0, data)
            else:
                child = os.open(name, flags, dir_fd=parent)
                try:
                    opened = os.fstat(child)
                    if stamp(info) != stamp(opened):
                        raise ValueError("input changed before read")
                    if stat.S_ISDIR(opened.st_mode):
                        walk(child, relative + "/")
                    elif stat.S_ISREG(opened.st_mode):
                        if name == ".lock" and opened.st_nlink != 1:
                            raise ValueError("hardlinked installer artifact")
                        if not links and (
                            name.endswith(".pyc")
                            or "__pycache__" in relative.split("/")
                        ):
                            raise ValueError("bytecode input")
                        chunks = []
                        while chunk := os.read(
                            child, min(1024 * 1024, MAX_BYTES - total + 1)
                        ):
                            total += len(chunk)
                            if total > MAX_BYTES:
                                raise ValueError("input size limit")
                            chunks.append(chunk)
                        result[relative] = (
                            "file",
                            stat.S_IMODE(opened.st_mode),
                            b"".join(chunks),
                        )
                    else:
                        raise ValueError("special input file")
                    if stamp(opened) != stamp(os.fstat(child)):
                        raise ValueError("input changed during read")
                finally:
                    os.close(child)
                if stamp(info) != stamp(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                ):
                    raise ValueError("input replaced during read")
            if total > MAX_BYTES:
                raise ValueError("input size limit")
        if stamp(before) != stamp(os.fstat(parent)):
            raise ValueError("directory changed during snapshot")

    try:
        for part in path.absolute().parts[1:]:
            if part in {".", ".."}:
                raise ValueError("noncanonical input root")
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        walk(fd, "")
    except OSError as error:
        raise ValueError("unreadable or symlink input") from error
    finally:
        os.close(fd)
    if links:
        check_links(result)
    return result


def check_links(entries: dict[str, Entry]) -> None:
    for name, (kind, _, data) in entries.items():
        if kind != "link":
            continue
        seen = {name}
        current = name
        target = data.decode()
        while True:
            if target.startswith("/") or "\\" in target:
                raise ValueError("absolute archive link")
            current = posixpath.normpath(
                posixpath.join(posixpath.dirname(current), target)
            )
            if not canonical(current) or current in seen:
                raise ValueError("escaping or cyclic archive link")
            seen.add(current)
            # Directory links are forbidden: no indirect traversal through links.
            if any(
                current.startswith(p + "/")
                for p, e in entries.items()
                if e[0] == "link"
            ):
                raise ValueError("indirect archive link")
            entry = entries.get(current)
            if entry is None:
                raise ValueError("dangling archive link")
            if entry[0] == "file":
                break
            target = entry[2].decode()


def archive_entries(data: bytes, *, links: bool = False) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    names: set[str] = set()
    total = 0
    # Bound decompression, including tar headers and padding, before parsing.
    import gzip

    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        expanded = compressed.read(MAX_BYTES + 1)
    if len(expanded) > MAX_BYTES:
        raise ValueError("archive expansion limit")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for member in archive:
            name = member.name.rstrip("/") if member.isdir() else member.name
            total += member.size
            if (
                not canonical(name)
                or name in names
                or len(names) >= MAX_FILES
                or total > MAX_BYTES
            ):
                raise ValueError("unsafe, duplicate or oversized archive")
            names.add(name)
            if member.isdir():
                continue
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("missing archive member")
                entries[name] = (
                    "file",
                    stat.S_IMODE(member.mode),
                    stream.read(MAX_BYTES + 1),
                )
            elif links and member.issym():
                entries[name] = ("link", 0, member.linkname.encode())
            else:
                raise ValueError("special archive member")
    if links:
        check_links(entries)
    return entries


def files(entries: dict[str, Entry]) -> dict[str, bytes]:
    if any(entry[0] != "file" for entry in entries.values()):
        raise ValueError("expected only regular files")
    return {name: entry[2] for name, entry in entries.items()}


def recipe_for(root: Path, wheel_hashes: dict[str, str]) -> dict[str, Any]:
    """Inert recipe only: constructing this object executes nothing."""
    root = root.absolute()
    if not canonical(str(root)[1:]) or not re.fullmatch(
        r"/[A-Za-z0-9_./-]+", str(root)
    ):
        raise ValueError("unsupported task root")
    interpreter = str(root / "runtime/python/bin/python3.14")
    prefix = [
        str(root / "uv-release/uv-x86_64-unknown-linux-gnu/uv"),
        "--no-config",
        "pip",
        "install",
        "--offline",
        "--no-index",
        "--find-links",
        str(root / "wheels"),
        "--require-hashes",
        "-r",
        str(root / "requirements.lock"),
        "--target",
    ]
    suffix = [
        "--python",
        interpreter,
        "--python-platform",
        "x86_64-unknown-linux-gnu",
        "--python-version",
        "3.14",
        "--only-binary",
        ":all:",
        "--no-compile-bytecode",
    ]
    return {
        "schema": "runspindle.dspy-x64-preparation-recipe.v1",
        "platform": "linux/x86_64",
        "python_version": "3.14.10",
        "dspy_version": "3.1.0",
        "root": str(root),
        "interpreter": interpreter,
        "argv": [
            prefix + [str(root / target)] + suffix for target in ("deps-a", "deps-b")
        ],
        "environment": {
            "HOME": str(root / "home"),
            "TMPDIR": str(root / "tmp"),
            "PATH": str(root / "runtime/python/bin") + ":/usr/bin:/bin",
            "LC_ALL": "C",
            "TZ": "UTC",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "UV_NO_CACHE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_KEYRING_PROVIDER": "disabled",
        },
        "cwd": str(root),
        "umask": "022",
        "network": "installer_offline_flags_only_not_os_isolation",
        "dependency_lock_sha256": LOCK_SHA256,
        "uv": {
            "version": "0.10.6",
            "url": UV_URL,
            "archive_sha256": UV_SHA256,
            "checksum_url": UV_URL + ".sha256",
            "checksum_sha256": UV_CHECKSUM_SHA256,
        },
        "python": {
            "version": "3.14.10",
            "release": "20261110",
            "url": PYTHON_URL,
            "archive_sha256": PYTHON_SHA256,
        },
        "wheels": wheel_hashes,
    }


def compatible_tag(tag: str) -> bool:
    parts = tag.split("-")
    if len(parts) != 3:
        return False
    python, abi, platform = parts
    if platform == "any":
        return python in {"py3", "cp314"} and abi == "none"
    match = re.fullmatch(r"manylinux_(\d+)_(\d+)_x86_64", platform)
    platform_ok = platform in {
        "manylinux1_x86_64",
        "manylinux2010_x86_64",
        "manylinux2014_x86_64",
    } or bool(match and int(match[1]) == 2 and int(match[2]) <= 28)
    # Profile requires Linux x86_64 glibc >= 2.28, non-free-threaded CPython.
    if not platform_ok:
        return False
    if python == "cp314" and abi in {"cp314", "none"}:
        return True
    return bool(
        re.fullmatch(r"cp3\d+", python) and int(python[3:]) <= 14 and abi == "abi3"
    )


def check_wheel_paths(wheels: dict[str, bytes]) -> None:
    for data in wheels.values():
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise ValueError("wheel member limit")
            for member in archive.infolist():
                name = (
                    member.filename.rstrip("/") if member.is_dir() else member.filename
                )
                if not canonical(name):
                    raise ValueError("unsafe wheel member path or depth")


def check_wheel_tags(wheels: dict[str, bytes]) -> None:
    for filename, data in wheels.items():
        parts = filename[:-4].split("-")
        if len(parts) not in {5, 6} or not filename.endswith(".whl"):
            raise ValueError("opaque wheel filename")
        filename_tags = {
            f"{p}-{a}-{t}"
            for p in parts[-3].split(".")
            for a in parts[-2].split(".")
            for t in parts[-1].split(".")
        }
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            metadata = [
                x
                for x in archive.namelist()
                if re.fullmatch(r"[^/]+\.dist-info/WHEEL", x)
            ]
            if len(metadata) != 1:
                raise ValueError("wheel must contain one WHEEL")
            wheel = BytesParser().parsebytes(archive.read(metadata[0]))
            tags = set(wheel.get_all("Tag", []))
            if tags != filename_tags or not any(compatible_tag(tag) for tag in tags):
                raise ValueError("incompatible or inconsistent wheel tags")
            metas = [
                x
                for x in archive.namelist()
                if re.fullmatch(r"[^/]+\.dist-info/METADATA", x)
            ]
            identity = BytesParser().parsebytes(archive.read(metas[0]))
            if (
                common.normalize(parts[0]) != common.normalize(identity["Name"])
                or parts[1] != identity["Version"]
            ):
                raise ValueError("wheel filename identity mismatch")


def elf_x64(data: bytes) -> None:
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or data[18:20] != b"\x3e\x00":
        raise ValueError("expected little-endian x86_64 ELF binary")


def inventory(files: dict[str, bytes]) -> dict[str, Any]:
    """Same schema as the strict inventory, derived solely from audited bytes."""
    rows = []
    for name in sorted(files):
        if not re.fullmatch(r"[^/]+\.dist-info/METADATA", name):
            continue
        dist = name.split("/")[0]
        metadata = BytesParser().parsebytes(files[name])
        notices = {
            p: common.digest(data)
            for p, data in sorted(files.items())
            if p.startswith(dist + "/")
            and (
                any(x in Path(p).name.upper() for x in ("LICENSE", "NOTICE", "COPYING"))
                or "licenses" in Path(p).parts
            )
        }
        license_id = (
            metadata.get("License-Expression")
            or metadata.get("License")
            or "; ".join(
                c for c in metadata.get_all("Classifier", []) if c.startswith("License")
            )
        )
        if not license_id or (not notices and metadata["Name"] != "tokenizers"):
            raise ValueError("dependency rights evidence unavailable")
        rows.append(
            {
                "name": metadata["Name"],
                "version": metadata["Version"],
                "license": license_id,
                "notices": notices,
                "metadata_sha256": common.digest(files[name]),
            }
        )
    if len(rows) != 76 or not any(
        r["name"] == "dspy" and r["version"] == "3.1.0" for r in rows
    ):
        raise ValueError("incomplete dependency preparation")
    hashes = {p: common.digest(data) for p, data in sorted(files.items())}
    return {
        "schema_version": 1,
        "dspy": "3.1.0",
        "target": "linux/x86_64",
        "python": "3.14.10",
        "dependency_lock_sha256": LOCK_SHA256,
        "dependencies": rows,
        "installed_files": hashes,
        "tree_sha256": common.digest(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ),
        "execution": "not_run",
    }


def audit(root: Path, recipe: dict[str, Any]) -> dict[str, Any]:
    # Each validation consumes immutable captured bytes, never reopens its input.
    lock = read_input(root / "requirements.lock")
    wheels = files(snapshot(root / "wheels"))
    artifacts = files(snapshot(root / "artifacts"))
    uv = snapshot(root / "uv-release")
    runtime = snapshot(root / "runtime", links=True)
    first = snapshot(root / "deps-a")
    second = snapshot(root / "deps-b")
    if common.digest(lock) != LOCK_SHA256:
        raise ValueError("canonical lock mismatch")
    expected = recipe_for(
        root, {name: common.digest(data) for name, data in sorted(wheels.items())}
    )
    if json.dumps(recipe, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError("unsupported x64 recipe")
    check_wheel_paths(wheels)
    identities = common.wheel_identities(wheels, lock.decode())
    if len(identities) != 76 or ("dspy", "3.1.0") not in identities:
        raise ValueError("expected exact 76-package DSPy closure")
    check_wheel_tags(wheels)
    if set(artifacts) != {"uv.tar.gz", "uv.tar.gz.sha256", "python.tar.gz"}:
        raise ValueError("unexpected artifact set")
    for name, pin in (
        ("uv.tar.gz", UV_SHA256),
        ("uv.tar.gz.sha256", UV_CHECKSUM_SHA256),
        ("python.tar.gz", PYTHON_SHA256),
    ):
        if common.digest(artifacts[name]) != pin:
            raise ValueError("official archive pin mismatch")
    if artifacts["uv.tar.gz.sha256"].decode().split()[0] != UV_SHA256:
        raise ValueError("uv checksum mismatch")
    if uv != archive_entries(artifacts["uv.tar.gz"]) or runtime != archive_entries(
        artifacts["python.tar.gz"], links=True
    ):
        raise ValueError("installed tools differ from pinned archives")
    uv_binary = uv["uv-x86_64-unknown-linux-gnu/uv"][2]
    python_binary = runtime["python/bin/python3.14"][2]
    elf_x64(uv_binary)
    elf_x64(python_binary)
    if first != second:
        raise ValueError("independent dependency trees differ")
    installed = files(first)
    if first.get(".lock") != ("file", 0o666, b""):
        raise ValueError("expected empty regular mode-0666 installer .lock")
    common.check_record_bytes(
        {name: data for name, data in installed.items() if name != ".lock"}
    )
    interpreter = recipe["interpreter"]
    scripts = {}
    for name, data in installed.items():
        if name.startswith("bin/"):
            if (
                name.count("/") != 1
                or data.split(b"\n", 1)[0] != ("#!" + interpreter).encode()
            ):
                raise ValueError("generated script interpreter mismatch")
            scripts[name] = common.digest(data)
    manifest = inventory(installed)
    if {
        (common.normalize(row["name"]), row["version"])
        for row in manifest["dependencies"]
    } != identities:
        raise ValueError("installed identity mismatch")
    return {
        "schema": "runspindle.dspy-x64-preparation-candidate.v1",
        "status": "UNREVIEWED_CANDIDATE_NOT_EXECUTION_AUTHORITY",
        "framework_qualification": "not_run",
        "publisher_authentication": "not_established_by_local_hashes_or_recipe_urls",
        "preparation_observation": "independent_runs_and_host_identity_require_external_reviewed_receipts",
        "recipe": recipe,
        "candidate_manifest": manifest,
        "generated_scripts": scripts,
        "binary_sha256": {
            "uv": common.digest(uv_binary),
            "python": common.digest(python_binary),
        },
        "comparison": "exact_dependency_bytes_and_modes_equal",
        "installer_artifacts": {
            ".lock": {
                "sha256": common.digest(b""),
                "mode": "0666",
                "classification": "observed_uv_nonpackage_artifact",
            }
        },
        "runtime_archive": "exact_files_modes_and_opaque_contained_links_equal",
    }


def read_input(path: Path, limit: int = 1024 * 1024) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open("/", flags | os.O_DIRECTORY)
    try:
        parts = path.absolute().parts[1:]
        for index, part in enumerate(parts):
            if part in {".", ".."}:
                raise ValueError("noncanonical file path")
            child = os.open(
                part,
                flags | (os.O_DIRECTORY if index < len(parts) - 1 else 0),
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("expected regular input file")
        chunks = []
        size = 0
        while chunk := os.read(fd, min(1024 * 1024, limit - size + 1)):
            size += len(chunk)
            if size > limit:
                raise ValueError("file input size limit")
            chunks.append(chunk)
        if stamp(before) != stamp(os.fstat(fd)):
            raise ValueError("input changed during read")
        return b"".join(chunks)
    except OSError as error:
        raise ValueError("unreadable or symlink input file") from error
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(
                args.root,
                json.loads(
                    read_input(args.recipe), object_pairs_hook=common.unique_object
                ),
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
