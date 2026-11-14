#!/usr/bin/env python3
"""Acquire pinned wheels separately; verify before network-disabled execution.

Preparation never imports a dependency. No source builds or implicit downloads
are permitted in the execution lane.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import signal

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "python@sha256:0e58fc4a4575979bfce26735236422a48b38dae99d72b9d72a15e8e3fe0f7f93"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(base):
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


def execute(prepared):
    base = Path(prepared).resolve()
    declared = json.loads((ROOT / "offline/dependencies.json").read_bytes())
    if inventory(base) != declared:
        raise ValueError("prepared dependency bytes differ from reviewed manifest")
    toolchain = json.loads((ROOT / "offline/toolchain.json").read_bytes())
    if toolchain["image"] != IMAGE or toolchain["target"] != "linux/arm64":
        raise ValueError("unqualified execution target")
    if any(
        sha(ROOT / relative) != expected
        for relative, expected in toolchain["notices"].items()
    ):
        raise ValueError("toolchain notice mismatch")
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
                "tools/test_dspy_local.py",
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
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = execute(args.prepared)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
