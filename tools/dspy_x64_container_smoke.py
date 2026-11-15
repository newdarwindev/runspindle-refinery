"""Opt-in real-framework smoke. Run only in the reviewed immutable container.

External owner authorization, image review and launcher confinement are required.
This script neither approves itself nor replaces either ARM64 qualification gate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import socket
import stat
import sys
from pathlib import Path
from typing import Any

CANDIDATE_SHA256 = "d1660d0927cd2b325a50b426af7cd608311dafa367dd453f51d21418a7ad285f"


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate contract field")
        result[key] = value
    return result


def hash_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def tree(root: Path) -> dict[str, dict[str, Any]]:
    """Inspect immutable image trees without following runtime link entries."""
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("image tree root must be a directory")
    result: dict[str, dict[str, Any]] = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                row: dict[str, Any] = {"kind": "link", "target": os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                row = {
                    "kind": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "sha256": hash_file(path),
                }
            elif stat.S_ISDIR(info.st_mode):
                row = {"kind": "directory", "mode": stat.S_IMODE(info.st_mode)}
            else:
                raise ValueError("unexpected image tree node")
            result[relative] = row
            if len(result) > 30000:
                raise ValueError("image tree node limit")
    return result


def verify(
    contract_bytes: bytes, expected: str, root: Path = Path("/")
) -> dict[str, Any]:
    if (
        len(contract_bytes) > 16777216
        or hashlib.sha256(contract_bytes).hexdigest() != expected
    ):
        raise ValueError("contract digest or size differs")
    contract: dict[str, Any] = json.loads(contract_bytes, object_pairs_hook=unique)
    if contract["schema"] != "runspindle.dspy-x64-container-smoke.v1":
        raise ValueError("unsupported smoke contract")
    if not re.fullmatch(r"[0-9a-f]{40}", contract["source_sha"]):
        raise ValueError("exact source revision required")
    if set(contract["trees"]) != {"runtime", "deps", "source"}:
        raise ValueError("complete image trees required")
    for name, entries in contract["trees"].items():
        if tree(root / name) != entries:
            raise ValueError("image tree differs: " + name)
    native: dict[str, Any] = contract["native"]
    if (
        not native
        or contract["native_scope"]
        != "static_DT_NEEDED_closure_dynamic_import_unqualified"
    ):
        raise ValueError("reviewed native closure required")
    for name, expected_hash in native.items():
        if not re.fullmatch(r"lib(?:64|/x86_64-linux-gnu)/[A-Za-z0-9_.+-]+", name):
            raise ValueError("native path outside fixed library roots")
        path = root / name
        if not stat.S_ISREG(path.lstat().st_mode) or hash_file(path) != expected_hash:
            raise ValueError("native library differs")
    candidate_path = root / "evidence/candidate.json"
    if (
        contract["candidate_sha256"] != CANDIDATE_SHA256
        or hash_file(candidate_path) != CANDIDATE_SHA256
    ):
        raise ValueError("candidate identity differs")
    candidate = json.loads(candidate_path.read_bytes(), object_pairs_hook=unique)
    if candidate["status"] != "UNREVIEWED_CANDIDATE_NOT_EXECUTION_AUTHORITY":
        raise ValueError("candidate classification differs")
    manifest = candidate["candidate_manifest"]
    if (
        manifest["dependency_lock_sha256"]
        != "830486665f562043c93297ff9da93db9b379c13cf12eba680b320dc1c5b4b525"
    ):
        raise ValueError("76-package canonical lock differs")
    if len(manifest["dependencies"]) != 76 or manifest["dspy"] != "3.1.0":
        raise ValueError("framework profile differs")
    observed = {
        name: row["sha256"]
        for name, row in contract["trees"]["deps"].items()
        if row["kind"] == "file"
    }
    if observed != manifest["installed_files"]:
        raise ValueError("candidate dependency file inventory differs")
    return contract


def run(contract: dict[str, Any]) -> dict[str, Any]:
    if (
        sys.version_info[:3] != (3, 14, 10)
        or sys.platform != "linux"
        or os.uname().machine != "x86_64"
    ):
        raise ValueError("unqualified actual runtime")
    if os.getuid() != 65532 or sys.executable != "/runtime/python/bin/python3.14":
        raise ValueError("unqualified execution identity")
    sys.path[:0] = ["/source", "/deps"]
    from tools.dspy_x64_container_contract import FIXTURE, digest, encoded

    if contract["fixture_sha256"] != digest(encoded(FIXTURE)):
        raise ValueError("synthetic fixture differs")
    attempts: list[str] = []

    def denied(*args: Any, **kwargs: Any) -> Any:
        attempts.append("socket_attempt")
        raise RuntimeError("network forbidden")

    from unittest.mock import patch

    with (
        patch.object(socket.socket, "connect", denied),
        patch.object(socket.socket, "connect_ex", denied),
        patch.object(socket, "create_connection", denied),
    ):
        ev: Any = importlib.import_module("refinery.evaluation")
        local: Any = importlib.import_module("refinery.dspy_local")
        provider = local.LocalDSPyProvider(
            (ev.Completion(FIXTURE["answer"].encode(), 1, 0, True),),
            clock=ev.VirtualClock(),
            dependency_sha256=contract["candidate_sha256"],
        )
        request = ev.Request(
            "baseline", "synthetic-smoke", "revision", "a" * 64, "b" * 64, "c" * 64, 1
        )
        reply = provider.complete(request)
    if (
        attempts
        or reply.content != FIXTURE["answer"].encode()
        or len(provider.calls) != 1
        or len(provider.lm_calls) != 1
    ):
        raise ValueError("actual framework smoke failed")
    return {
        "status": "passed",
        "evidence_class": "actual_framework_synthetic_completion_smoke",
        "candidate_sha256": contract["candidate_sha256"],
        "source_sha": contract["source_sha"],
        "actual_framework": "DSPy 3.1.0",
        "provider_calls": 1,
        "lm_calls": 1,
        "quality": "synthetic_only",
        "bootstrap": "not_run",
        "restart": "not_run",
        "live_provider": "not_run",
        "serving": "not_run",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-sha256", required=True)
    arguments = parser.parse_args()
    contract = verify(
        Path("/evidence/contract.json").read_bytes(), arguments.contract_sha256
    )
    print(json.dumps(run(contract), sort_keys=True))


if __name__ == "__main__":
    main()
