#!/usr/bin/env python3
"""Audit a proposed closure without installing, importing or promoting it.

All provenance is a review input, not a claim of publisher authentication.
The JSON emitted on stdout is deliberately not an execution manifest.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from tools import offline_dspy

ROOT = Path(__file__).resolve().parents[1]
INTERPRETER = "/usr/local/bin/python3.14"
ARGV = [
    "/tools/uv",
    "--no-config",
    "pip",
    "install",
    "--offline",
    "--no-index",
    "--find-links",
    "/wheels",
    "--require-hashes",
    "-r",
    "/work/offline/requirements.lock",
    "--target",
    "/deps",
    "--python",
    INTERPRETER,
    "--python-platform",
    "aarch64-unknown-linux-gnu",
    "--python-version",
    "3.14",
    "--only-binary",
    ":all:",
    "--no-compile-bytecode",
]
ENVIRONMENT = {
    "HOME": "/tmp",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LC_ALL": "C",
    "TZ": "UTC",
    "PYTHONDONTWRITEBYTECODE": "1",
    "UV_NO_CACHE": "1",
    "UV_PYTHON_DOWNLOADS": "never",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


MAX_BYTES = 1024 * 1024 * 1024
MAX_FILES = 30000


def snapshot(base: Path) -> dict[str, bytes]:
    """Read once with descriptor-relative, no-follow opens (Linux/POSIX only)."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    root = os.open("/", flags | os.O_DIRECTORY)
    result: dict[str, bytes] = {}
    total = 0
    nodes = 0

    def walk(fd: int, prefix: str) -> None:
        nonlocal total, nodes
        if prefix.count("/") > 64:
            raise ValueError("input depth limit")
        for name in sorted(os.listdir(fd)):
            nodes += 1
            if nodes > MAX_FILES:
                raise ValueError("input node limit")
            child = os.open(name, flags, dir_fd=fd)
            try:
                mode = os.fstat(child).st_mode
                relative = prefix + name
                if stat.S_ISDIR(mode):
                    if name == "__pycache__":
                        raise ValueError("bytecode is not a preparation input")
                    walk(child, relative + "/")
                elif stat.S_ISREG(mode):
                    if name.endswith(".pyc"):
                        raise ValueError("bytecode is not a preparation input")
                    if len(result) >= MAX_FILES:
                        raise ValueError("too many input files")
                    chunks: list[bytes] = []
                    while chunk := os.read(
                        child, min(1024 * 1024, MAX_BYTES - total + 1)
                    ):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError("input size limit")
                        chunks.append(chunk)
                    result[relative] = b"".join(chunks)
                else:
                    raise ValueError("special files are not candidate inputs")
            finally:
                os.close(child)

    try:
        for part in base.absolute().parts[1:]:
            if part in {".", ".."}:
                raise ValueError("noncanonical input directory")
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=root)
            os.close(root)
            root = child
        walk(root, "")
    except OSError as error:
        raise ValueError("unreadable input or symlinks in input tree") from error
    finally:
        os.close(root)
    return result


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_record_bytes(files: dict[str, bytes]) -> None:
    coverage: set[str] = set()
    for record, data in files.items():
        if not re.fullmatch(r"[^/]+\.dist-info/RECORD", record):
            continue
        seen: set[str] = set()
        for row in csv.reader(io.StringIO(data.decode())):
            if len(row) != 3:
                raise ValueError("invalid RECORD row")
            name, checksum, size = row
            if (
                not name
                or name.startswith("/")
                or any(p in {"", ".", ".."} for p in name.split("/"))
            ):
                raise ValueError("escaping or noncanonical RECORD path")
            if name in seen or name in coverage:
                raise ValueError("duplicate RECORD path")
            if name not in files:
                raise ValueError("missing RECORD file")
            seen.add(name)
            coverage.add(name)
            if name == record:
                if checksum or size:
                    raise ValueError("RECORD must have an empty self hash")
            else:
                expected = "sha256=" + base64.urlsafe_b64encode(
                    hashlib.sha256(files[name]).digest()
                ).decode().rstrip("=")
                if checksum != expected or size != str(len(files[name])):
                    raise ValueError("RECORD hash or size mismatch")
        if record not in seen:
            raise ValueError("missing RECORD self row")
    if coverage != set(files):
        raise ValueError("unrecorded installed files")


def scripts_from_bytes(files: dict[str, bytes]) -> dict[str, str]:
    scripts = {}
    for name, data in files.items():
        if name.startswith("bin/"):
            if (
                name.count("/") != 1
                or data.split(b"\n", 1)[0] != ("#!" + INTERPRETER).encode()
            ):
                raise ValueError("unsupported generated script interpreter")
            scripts[name.removeprefix("bin/")] = digest(data)
    return scripts


def check_records(base: Path) -> dict[str, str]:
    files = snapshot(base)
    check_record_bytes(files)
    return scripts_from_bytes(files)


def inventory_bytes(files: dict[str, bytes]) -> dict[str, Any]:
    """Same schema as the strict inventory, derived solely from audited bytes."""
    rows = []
    for name in sorted(files):
        if not re.fullmatch(r"[^/]+\.dist-info/METADATA", name):
            continue
        dist = name.split("/")[0]
        metadata = BytesParser().parsebytes(files[name])
        notices = {
            p: digest(data)
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
                "metadata_sha256": digest(files[name]),
            }
        )
    if len(rows) != 76 or not any(
        r["name"] == "dspy" and r["version"] == "3.1.0" for r in rows
    ):
        raise ValueError("incomplete dependency preparation")
    hashes = {p: digest(data) for p, data in sorted(files.items())}
    return {
        "schema_version": 1,
        "dspy": "3.1.0",
        "target": "linux/arm64",
        "python": "3.14",
        "dependency_lock_sha256": sha(ROOT / "offline/requirements.lock"),
        "dependencies": rows,
        "installed_files": hashes,
        "tree_sha256": digest(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ),
        "execution": "not_run",
    }


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_identities(files: dict[str, bytes], lock: str) -> set[tuple[str, str]]:
    requirements: dict[tuple[str, str], set[str]] = {}
    current = None
    for line in lock.splitlines():
        match = re.match(r"^([a-zA-Z0-9_.-]+)==([^ ;\\]+)", line)
        if match:
            current = (normalize(match[1]), match[2])
            requirements[current] = set()
        for value in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
            if current is None:
                raise ValueError("unbound lock hash")
            requirements[current].add(value)
    found: set[tuple[str, str]] = set()
    for name, data in files.items():
        if "/" in name or not name.endswith(".whl"):
            raise ValueError("wheelhouse must contain only flat wheel files")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            contents: dict[str, bytes] = {}
            total = 0
            entries = archive.infolist()
            if len(entries) > MAX_FILES:
                raise ValueError("wheel member limit")
            member_names: set[str] = set()
            for entry in entries:
                name = entry.filename.rstrip("/")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (
                    not name
                    or name.startswith("/")
                    or any(p in {"", ".", ".."} for p in name.split("/"))
                    or name in member_names
                ):
                    raise ValueError("unsafe or duplicate wheel member")
                member_names.add(name)
                expected_mode = stat.S_IFDIR if entry.is_dir() else stat.S_IFREG
                if mode not in {0, expected_mode}:
                    raise ValueError("special wheel member")
                if entry.is_dir():
                    continue
                total += entry.file_size
                if total > MAX_BYTES or len(contents) >= MAX_FILES:
                    raise ValueError("wheel size limit")
                if entry.filename in contents or stat.S_ISLNK(
                    entry.external_attr >> 16
                ):
                    raise ValueError("duplicate or symlink wheel member")
                contents[entry.filename] = archive.read(entry)
        check_record_bytes(contents)
        metadata_files = [
            p for p in contents if re.fullmatch(r"[^/]+\.dist-info/METADATA", p)
        ]
        if len(metadata_files) != 1:
            raise ValueError("wheel must contain one distribution")
        metadata = BytesParser().parsebytes(contents[metadata_files[0]])
        identity = (normalize(metadata.get("Name", "")), metadata.get("Version", ""))
        if identity in found or digest(data) not in requirements.get(identity, set()):
            raise ValueError("duplicate or unlocked wheel distribution")
        found.add(identity)
    if found != set(requirements) or not found:
        raise ValueError("incomplete wheelhouse")
    return found


def check_recipe(
    recipe: dict[str, Any], artifacts: Path, wheels: Path
) -> set[tuple[str, str]]:
    toolchain = json.loads((ROOT / "offline/toolchain.json").read_text())
    required = {
        "schema_version": 1,
        "image": offline_dspy.IMAGE,
        "platform": "linux/arm64",
        "python_version": toolchain["version"],
        "interpreter": INTERPRETER,
        "argv": ARGV,
        "environment": ENVIRONMENT,
        "cwd": "/work",
        "umask": "022",
        "network": "none",
        "dependency_lock_sha256": sha(ROOT / "offline/requirements.lock"),
        "toolchain_sha256": sha(ROOT / "offline/toolchain.json"),
    }
    if set(recipe) != set(required) | {"uv", "wheels"}:
        raise ValueError("missing or unknown recipe fields")
    if any(
        json.dumps(recipe[key], sort_keys=True) != json.dumps(value, sort_keys=True)
        for key, value in required.items()
    ):
        raise ValueError("unsupported preparation recipe")
    uv = recipe["uv"]
    if not isinstance(uv, dict) or set(uv) != {
        "version",
        "url",
        "archive_sha256",
        "binary_sha256",
        "checksum_url",
        "checksum_sha256",
    }:
        raise ValueError("incomplete uv provenance")
    if not isinstance(uv["version"], str) or not re.fullmatch(
        r"\d+\.\d+\.\d+", uv["version"]
    ):
        raise ValueError("uv must have an exact release version")
    expected_url = (
        "https://github.com/astral-sh/uv/releases/download/"
        + uv["version"]
        + "/uv-aarch64-unknown-linux-gnu.tar.gz"
    )
    if uv["url"] != expected_url:
        raise ValueError("expected official arm64 uv release URL")
    if uv["checksum_url"] != expected_url + ".sha256":
        raise ValueError("expected official release checksum URL")
    artifact_bytes = snapshot(artifacts)
    checksum = artifact_bytes["uv.tar.gz.sha256"]
    if digest(checksum) != uv["checksum_sha256"]:
        raise ValueError("checksum evidence hash mismatch")
    tokens = checksum.decode().strip().split()
    if not tokens or tokens[0] != uv["archive_sha256"]:
        raise ValueError("release checksum does not match archive")
    for name, field in (("uv.tar.gz", "archive_sha256"), ("uv", "binary_sha256")):
        if digest(artifact_bytes[name]) != uv[field]:
            raise ValueError("uv artifact hash mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(artifact_bytes["uv.tar.gz"])) as compressed:
        expanded = compressed.read(MAX_BYTES + 1)
    if len(expanded) > MAX_BYTES:
        raise ValueError("uv archive expansion limit")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        binary = None
        total = 0
        names: set[str] = set()
        for index, member in enumerate(archive):
            if (
                member.name.startswith("/")
                or any(p in {".", ".."} for p in member.name.split("/"))
                or member.name in names
                or not (member.isdir() or member.isfile())
            ):
                raise ValueError("unsafe or duplicate uv archive member")
            names.add(member.name)
            total += member.size
            if index >= MAX_FILES or total > MAX_BYTES:
                raise ValueError("uv archive size limit")
            if member.name == "uv-aarch64-unknown-linux-gnu/uv":
                if binary is not None or not member.isfile():
                    raise ValueError("uv archive has no unique regular binary")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("missing uv archive binary")
                binary = stream.read(MAX_BYTES + 1)
        if binary is None or digest(binary) != uv["binary_sha256"]:
            raise ValueError("binary differs from release archive")
    wheel_bytes = snapshot(wheels)
    if recipe["wheels"] != {p: digest(data) for p, data in wheel_bytes.items()}:
        raise ValueError("wheelhouse differs from recipe")
    return wheel_identities(
        wheel_bytes, (ROOT / "offline/requirements.lock").read_text()
    )


def candidate(
    base: Path, recipe: dict[str, Any], artifacts: Path, wheels: Path
) -> dict[str, Any]:
    identities = check_recipe(recipe, artifacts, wheels)
    files = snapshot(base)
    check_record_bytes(files)
    scripts = scripts_from_bytes(files)
    manifest = inventory_bytes(files)
    if {
        (normalize(r["name"]), r["version"]) for r in manifest["dependencies"]
    } != identities:
        raise ValueError("installed distributions differ from selected wheels")
    canonical = json.loads((ROOT / "offline/dependencies.json").read_text())
    return {
        "schema": "runspindle.dspy-preparation-candidate.v1",
        "status": "unreviewed_candidate_not_execution_authority",
        "provenance": "operator_supplied_not_authenticated_by_this_auditor",
        "framework_qualification": "not_run",
        "recipe": recipe,
        "generated_scripts": scripts,
        "candidate_manifest": manifest,
        "matches_canonical_manifest": manifest == canonical,
        "canonical_manifest_sha256": sha(ROOT / "offline/dependencies.json"),
    }


def read_recipe(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open("/", flags | os.O_DIRECTORY)
    try:
        parts = path.absolute().parts[1:]
        for index, part in enumerate(parts):
            if part in {".", ".."}:
                raise ValueError("noncanonical recipe path")
            child = os.open(
                part,
                flags | (os.O_DIRECTORY if index < len(parts) - 1 else 0),
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("recipe must be a regular file")
        chunks = []
        size = 0
        while data := os.read(fd, 1024 * 1024 + 1 - size):
            size += len(data)
            if size > 1024 * 1024:
                raise ValueError("recipe size limit")
            chunks.append(data)
        value = json.loads(b"".join(chunks), object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError("recipe must be a JSON object")
        return value
    except OSError as error:
        raise ValueError("unreadable recipe or symlink") from error
    finally:
        os.close(fd)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--uv-artifacts", type=Path, required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            candidate(
                args.prepared,
                read_recipe(args.recipe),
                args.uv_artifacts,
                args.wheels,
            ),
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
