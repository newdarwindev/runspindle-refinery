#!/usr/bin/env python3
"""Acquire pinned wheels separately; verify before network-disabled execution.

Preparation never imports a dependency. No source builds or implicit downloads
are permitted in the execution lane.
"""

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import uuid
from email.parser import BytesParser
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "python@sha256:0e58fc4a4575979bfce26735236422a48b38dae99d72b9d72a15e8e3fe0f7f93"


# Updating this pin is an explicit reviewed source change, not a caller option.
ADOPTION_SHA256 = "e01ba2440ef2317ba85c911d4ce8ce44724b63ed374272cab8f94c84511fd206"
ADOPTION_FILES = frozenset(
    {
        "offline/dependencies.json",
        "offline/historical/dependencies-before-linux-native-20261118.json",
        "offline/linux-native-candidate/dependencies.json",
        "offline/linux-native-candidate/recipe.json",
        "offline/linux-native-candidate/receipt.json",
        "offline/linux-native-candidate/wheelhouse.json",
        "offline/requirements.lock",
        "offline/toolchain.json",
        "offline/python-LICENSE",
        "offline/tokenizers-LICENSE",
    }
)


def source_bytes(relative: str) -> bytes:
    """Capture bounded source bytes without accepting symlink aliases."""
    path = ROOT.absolute() / relative
    if ".." in path.parts:
        raise ValueError("noncanonical adoption source path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(path.parts[1:]) - 1:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
            raise ValueError("invalid adoption source file")
        chunks = []
        size = 0
        while data := os.read(fd, min(1024 * 1024, 8 * 1024 * 1024 + 1 - size)):
            chunks.append(data)
            size += len(data)
            if size > 8 * 1024 * 1024:
                raise ValueError("adoption source size limit")
        return b"".join(chunks)
    except OSError as error:
        raise ValueError("missing or unsafe adoption source file") from error
    finally:
        os.close(fd)


def json_object(data: bytes) -> dict[str, Any]:
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("adoption source must be a JSON object")
    return value


def reviewed_closure() -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_bytes = source_bytes("offline/dependency-adoption.json")
    if hashlib.sha256(receipt_bytes).hexdigest() != ADOPTION_SHA256:
        raise ValueError("unreviewed dependency adoption receipt")
    receipt = json_object(receipt_bytes)
    if set(receipt["files"]) != ADOPTION_FILES:
        raise ValueError("adoption closure paths changed")
    captured = {name: source_bytes(name) for name in sorted(ADOPTION_FILES)}
    if any(
        hashlib.sha256(data).hexdigest() != receipt["files"][name]
        for name, data in captured.items()
    ):
        raise ValueError("adopted source closure bytes changed")
    if (
        captured["offline/dependencies.json"]
        != captured["offline/linux-native-candidate/dependencies.json"]
    ):
        raise ValueError("active manifest differs from reviewed candidate")
    declared = json_object(captured["offline/dependencies.json"])
    toolchain = json_object(captured["offline/toolchain.json"])
    if (
        toolchain["image"] != IMAGE
        or toolchain["target"] != "linux/arm64"
        or toolchain["version"] != "Python 3.14.0"
    ):
        raise ValueError("unqualified adopted execution target")
    return declared, toolchain


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reject_unsafe_paths(base: Path) -> None:
    """Reject aliases and non-file payloads before hashing or container dispatch.

    The operator must keep the tree immutable during admission and execution;
    this preflight is not a concurrent host-writer exclusion mechanism.
    """
    absolute = base.absolute()
    if ".." in absolute.parts:
        raise ValueError("noncanonical prepared dependency path")

    def unreadable(error: OSError) -> None:
        raise ValueError("unreadable prepared dependency directory") from error

    try:
        for parent in (*reversed(absolute.parents), absolute):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise ValueError("prepared root and ancestors must be real directories")
        for directory, names, files in os.walk(
            absolute, followlinks=False, onerror=unreadable
        ):
            for name in names + files:
                mode = (Path(directory) / name).lstat().st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError("symlink or special file in prepared dependencies")
    except OSError as error:
        raise ValueError("unreadable prepared dependency path") from error


def reject_bytecode(base: Path) -> None:
    # -B/PYTHONDONTWRITEBYTECODE disables writes, not reads of cached code.
    # Match the preparation contract before comparing the reviewed inventory.
    for path in base.rglob("*"):
        if path.name == "__pycache__" or path.suffix == ".pyc":
            raise ValueError("bytecode is not a preparation input")


def inventory(base):
    reject_unsafe_paths(base)
    reject_bytecode(base)
    rows = []
    for dist in sorted(base.glob("*.dist-info")):
        metadata = BytesParser().parsebytes((dist / "METADATA").read_bytes())
        files = [
            p
            for p in dist.rglob("*")
            if p.is_file()
            and (
                any(x in p.name.upper() for x in ("LICENSE", "NOTICE", "COPYING"))
                or "licenses" in p.parts
            )
        ]
        license_id = (
            metadata.get("License-Expression")
            or metadata.get("License")
            or "; ".join(
                c for c in metadata.get_all("Classifier", []) if c.startswith("License")
            )
        )
        if not license_id or (not files and metadata["Name"] != "tokenizers"):
            raise ValueError(
                "dependency rights evidence unavailable: " + metadata["Name"]
            )
        rows.append(
            {
                "name": metadata["Name"],
                "version": metadata["Version"],
                "license": license_id,
                "notices": {str(p.relative_to(base)): sha(p) for p in files},
                "metadata_sha256": sha(dist / "METADATA"),
            }
        )
    files = {
        str(p.relative_to(base)): sha(p)
        for p in sorted(base.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    if len(rows) != 76 or not any(
        r["name"] == "dspy" and r["version"] == "3.1.0" for r in rows
    ):
        raise ValueError("incomplete dependency preparation")
    return {
        "schema_version": 1,
        "dspy": "3.1.0",
        "target": "linux/arm64",
        "python": "3.14",
        "dependency_lock_sha256": sha(ROOT / "offline/requirements.lock"),
        "dependencies": rows,
        "installed_files": files,
        "tree_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "execution": "not_run",
    }


def execute(prepared: str, suite: str = "predict") -> Any:
    if suite not in {"predict", "optimizer", "adversarial"}:
        raise ValueError("unqualified DSPy suite")
    base = Path(prepared).absolute()
    actual = cast(Any, inventory)(base)
    declared, toolchain = reviewed_closure()
    if actual != declared:
        raise ValueError("prepared dependency bytes differ from reviewed manifest")
    if toolchain["image"] != IMAGE or toolchain["target"] != "linux/arm64":
        raise ValueError("unqualified execution target")
    container = "runspindle-dspy-" + uuid.uuid4().hex
    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container,
                "--pull",
                "never",
                "--platform",
                "linux/arm64",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "1536m",
                "--cpus",
                "2",
                "--pids-limit",
                "256",
                "--tmpfs",
                "/tmp:rw,nosuid,size=512m",
                "-v",
                str(ROOT) + ":/work:ro",
                "-v",
                str(base) + ":/deps:ro",
                "-w",
                "/work",
                "-e",
                "PYTHONPATH=/deps:/work",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                "LITELLM_LOCAL_MODEL_COST_MAP=True",
                "-e",
                "HOME=/tmp",
                "-e",
                "DSPY_CACHEDIR=/tmp/dspy",
                "-e",
                "HF_HUB_OFFLINE=1",
                IMAGE,
                "python",
                "tools/test_dspy_local.py"
                if suite == "predict"
                else (
                    "tools/test_dspy_optimizer.py"
                    if suite == "optimizer"
                    else "tools/test_dspy_adversarial.py"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container], capture_output=True, timeout=30
        )
        if (
            subprocess.run(
                ["docker", "inspect", container], capture_output=True, timeout=30
            ).returncode
            == 0
        ):
            raise RuntimeError("offline DSPy container cleanup incomplete")
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("offline DSPy dependency lane failed")
    return json.loads(result.stdout)


if __name__ == "__main__":

    def interrupted(*_):
        raise KeyboardInterrupt("offline lane cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", default=str(ROOT / "local/python-deps"))
    parser.add_argument(
        "--suite", choices=("predict", "optimizer", "adversarial"), default="predict"
    )
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = execute(args.prepared, args.suite)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
