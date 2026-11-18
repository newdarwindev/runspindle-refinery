"""Sealed Python 3.13 GEPA strategy closure. Does not replace Bootstrap pins."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from tools.offline_dspy import source_bytes as source_bytes

ADOPTION_SHA256 = "e3abfed6281ec861fcdd42918f8a6c6ff881c128e20f989cca7cbeb6d36c5aa7"
IMAGE = "python@sha256:2d99ba6dc6a89d9a85e278d418f6cfbbf27a389482b69706019392dd22c4285f"
IMAGE_CONFIG = "sha256:03c6d4b159949d4769a04152b3fe56da0a54476214f025bb0358c4b496785d6b"
FILES = frozenset(
    {
        "offline/python313/recipe.json",
        "offline/python313/candidate-dependencies.json",
        "offline/python313/candidate-file-modes.json",
        "offline/requirements.lock",
        "docs/python313-input-evidence.json",
        "docs/python313-native-preparation.json",
        "offline/python-LICENSE",
    }
)
ROOT = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def reviewed() -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = source_bytes("offline/python313/gepa-adoption.json")
    if digest(receipt) != ADOPTION_SHA256:
        raise ValueError("unreviewed GEPA runtime adoption")
    declared = json.loads(receipt)
    if (
        set(declared["files"]) != FILES
        or declared["image"] != IMAGE
        or declared["image_config"] != IMAGE_CONFIG
    ):
        raise ValueError("GEPA adoption closure changed")
    captured = {name: source_bytes(name) for name in FILES}
    if any(digest(data) != declared["files"][name] for name, data in captured.items()):
        raise ValueError("GEPA adopted source bytes changed")
    inventory = json.loads(captured["offline/python313/candidate-dependencies.json"])
    modes = json.loads(captured["offline/python313/candidate-file-modes.json"])
    if (
        digest(canonical(inventory)) != declared["candidate_canonical_sha256"]
        or digest(canonical(modes)) != declared["modes_canonical_sha256"]
    ):
        raise ValueError("GEPA canonical inventory hashes changed")
    if inventory["python"] != "3.13.13" or inventory["installed_files"] != {
        name: row["sha256"] for name, row in modes.items() if row["type"] == "file"
    }:
        raise ValueError("GEPA adopted inventories disagree")
    return inventory, modes


def snapshot(path: Path) -> dict[str, Any]:
    """Descriptor-based file bytes AND directory metadata; no atomic-tree claim."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open("/", flags | os.O_DIRECTORY)
    result: dict[str, Any] = {}
    total = 0

    def stamp(info: os.stat_result) -> tuple[int, ...]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def walk(parent: int, relative: str) -> None:
        nonlocal total
        before = os.fstat(parent)
        result[relative or "."] = {
            "type": "directory",
            "mode": stat.S_IMODE(before.st_mode),
            "sha256": None,
        }
        for name in sorted(os.listdir(parent)):
            child_path = relative + "/" + name if relative else name
            if (
                name in {".", "..", "__pycache__"}
                or "\\" in name
                or child_path.count("/") > 64
                or len(result) >= 30000
                or name.endswith(".pyc")
            ):
                raise ValueError("GEPA unsafe prepared path")
            prior = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not (stat.S_ISREG(prior.st_mode) or stat.S_ISDIR(prior.st_mode)):
                raise ValueError("GEPA special prepared node")
            child = os.open(name, flags, dir_fd=parent)
            try:
                opened = os.fstat(child)
                if stamp(prior) != stamp(opened):
                    raise ValueError("GEPA prepared node changed")
                if stat.S_ISDIR(opened.st_mode):
                    walk(child, child_path)
                else:
                    if opened.st_nlink != 1:
                        raise ValueError("GEPA hardlinked prepared node")
                    hashed = hashlib.sha256()
                    while data := os.read(child, 1024 * 1024):
                        total += len(data)
                        if total > 1024 * 1024 * 1024:
                            raise ValueError("GEPA prepared size limit")
                        hashed.update(data)
                    result[child_path] = {
                        "type": "file",
                        "mode": stat.S_IMODE(opened.st_mode),
                        "sha256": hashed.hexdigest(),
                    }
                if stamp(opened) != stamp(os.fstat(child)):
                    raise ValueError("GEPA prepared bytes changed")
            finally:
                os.close(child)
            if stamp(prior) != stamp(
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            ):
                raise ValueError("GEPA prepared path changed")
        if stamp(before) != stamp(os.fstat(parent)):
            raise ValueError("GEPA prepared directory changed")

    try:
        for part in path.absolute().parts[1:]:
            if part in {".", ".."}:
                raise ValueError("GEPA noncanonical prepared root")
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        walk(fd, "")
    except OSError as exc:
        raise ValueError("GEPA unsafe or unreadable prepared input") from exc
    finally:
        os.close(fd)
    return result


def verify_prepared(path: Path) -> str:
    _, modes = reviewed()
    if snapshot(path) != modes:
        raise ValueError("GEPA prepared bytes or modes differ")
    return ADOPTION_SHA256


def runtime_identity() -> str:
    if (
        sys.version_info[:3] != (3, 13, 13)
        or sys.platform != "linux"
        or os.uname().machine != "aarch64"
        or sys.executable != "/usr/local/bin/python3.13"
    ):
        raise ValueError("unqualified GEPA Python or target")
    verified = verify_prepared(Path("/deps"))
    for name in ("dspy", "gepa"):
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin != "/deps/" + name + "/__init__.py":
            raise ValueError("GEPA framework import origin changed")
    sources = {
        name: digest(source_bytes(name))
        for name in (
            "refinery/gepa_runtime.py",
            "refinery/gepa_optimizer.py",
            "refinery/gepa_codec.py",
            "refinery/dspy_optimizer.py",
            "refinery/evaluation.py",
            "refinery/durable_evaluation.py",
        )
    }
    return digest(canonical({"adoption": verified, "sources": sources}))
