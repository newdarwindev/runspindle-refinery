#!/usr/bin/env python3
"""Audit separate ARM64 Python 3.13 inputs/outputs; never execute or promote."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from tools import dspy_preparation_candidate as common
from tools import dspy_x64_preparation_candidate as shared

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "offline/python313/recipe.json"
VERSION = (3, 13, 13)
INTERPRETER = "/usr/local/bin/python3.13"


def permits_python(value: str) -> bool:
    """Bounded numeric specifier subset used by this pinned closure.

    Unknown syntax rejects; this is not a general PEP 440 resolver.
    """
    if not value:
        raise ValueError("missing Requires-Python")
    for part in value.split(","):
        match = re.fullmatch(
            r"\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+){0,2})(\.\*)?\s*", part
        )
        if not match:
            raise ValueError("unsupported Requires-Python syntax")
        op, number, wildcard = match.groups()
        pieces = tuple(int(p) for p in number.split("."))
        if wildcard:
            if op not in {"==", "!="}:
                raise ValueError("unsupported Requires-Python wildcard")
            equal = VERSION[: len(pieces)] == pieces
            passed = equal if op == "==" else not equal
        else:
            target = pieces + (0,) * (3 - len(pieces))
            passed = {
                ">=": VERSION >= target,
                "<=": VERSION <= target,
                ">": VERSION > target,
                "<": VERSION < target,
                "==": VERSION == target,
                "!=": VERSION != target,
            }[op]
        if not passed:
            return False
    return True


def compatible_tag(tag: str) -> bool:
    parts = tag.split("-")
    if len(parts) != 3:
        return False
    python, abi, platform = parts
    if platform == "any":
        return python in {"py3", "cp313"} and abi == "none"
    match = re.fullmatch(r"manylinux_2_(\d+)_aarch64", platform)
    if platform != "manylinux2014_aarch64" and not (
        match and 17 <= int(match[1]) <= 28
    ):
        return False
    return (python == "cp313" and abi in {"cp313", "none"}) or bool(
        re.fullmatch(r"cp3\d+", python) and 2 <= int(python[3:]) <= 13 and abi == "abi3"
    )


def check_python_requirements(wheels: dict[str, bytes]) -> dict[str, str]:
    result = {}
    for filename, data in wheels.items():
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            paths = [
                p
                for p in archive.namelist()
                if re.fullmatch(r"[^/]+\.dist-info/METADATA", p)
            ]
            if len(paths) != 1:
                raise ValueError("expected one wheel METADATA")
            metadata = BytesParser().parsebytes(archive.read(paths[0]))
        values = metadata.get_all("Requires-Python", [])
        if len(values) != 1 or not permits_python(values[0]):
            raise ValueError("incompatible or ambiguous Requires-Python: " + filename)
        result[filename] = values[0]
    return result


def audit_inputs(artifacts: Path, wheels: Path) -> dict[str, Any]:
    recipe = common.read_recipe(PROFILE)
    if (
        common.sha(ROOT / "offline/requirements.lock")
        != recipe["dependency_lock_sha256"]
    ):
        raise ValueError("canonical lock changed")
    common.check_uv(recipe["uv"], artifacts)
    captured = shared.files(shared.snapshot(wheels))
    if recipe["wheels"] != {
        name: common.digest(data) for name, data in captured.items()
    }:
        raise ValueError("candidate wheelhouse differs")
    shared.check_wheel_paths(captured)
    identities = common.wheel_identities(
        captured, (ROOT / "offline/requirements.lock").read_text()
    )
    if len(identities) != 76 or ("dspy", "3.1.0") not in identities:
        raise ValueError("expected locked 76-distribution closure")
    shared.check_wheel_tags(captured, compatible_tag)
    requirements = check_python_requirements(captured)
    return {
        "schema": "runspindle.dspy-arm313-input-audit.v1",
        "status": "verified_input_bytes_not_preparation_or_execution_authority",
        "recipe": recipe,
        "requires_python": requirements,
        "dependency_resolution": "not_established_by_metadata_audit",
        "framework_qualification": "not_run",
    }


def compare_preparations(first: Path, second: Path) -> dict[str, Any]:
    if os.path.samefile(first, second):
        raise ValueError("two distinct preparation directories required")
    a, b = shared.snapshot(first), shared.snapshot(second)
    if a != b:
        raise ValueError("independent preparation bytes or modes differ")
    installed = shared.files(a)
    # uv's empty installer lock remains in the full comparison and inventory.
    if a.get(".lock") != ("file", 0o666, b""):
        raise ValueError("expected empty regular mode-0666 installer .lock")
    common.check_record_bytes({p: d for p, d in installed.items() if p != ".lock"})
    for name, data in installed.items():
        if name.startswith("bin/") and (
            name.count("/") != 1
            or data.split(b"\n", 1)[0] != ("#!" + INTERPRETER).encode()
        ):
            raise ValueError("generated interpreter differs")
    manifest = common.inventory_bytes(installed)
    manifest["python"] = "3.13.13"
    return {
        "status": "unreviewed_candidate_not_execution_authority",
        "candidate_manifest": manifest,
        "native_preparation": "requires_independently_reviewed_execution_receipts",
        "framework_qualification": "not_run",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv-artifacts", type=Path, required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--first", type=Path)
    parser.add_argument("--second", type=Path)
    args = parser.parse_args()
    if bool(args.first) != bool(args.second):
        parser.error("both independently prepared trees are required")
    report = audit_inputs(args.uv_artifacts, args.wheels)
    if args.first:
        report["comparison"] = compare_preparations(args.first, args.second)
        expected = {
            (common.normalize(r["name"]), r["version"])
            for r in report["comparison"]["candidate_manifest"]["dependencies"]
        }
        canonical = json.loads((ROOT / "offline/dependencies.json").read_text())
        if expected != {
            (common.normalize(r["name"]), r["version"])
            for r in canonical["dependencies"]
        }:
            raise ValueError("prepared distribution identities differ")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
