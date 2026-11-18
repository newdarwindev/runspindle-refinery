#!/usr/bin/env python3
"""Dedicated sealed GEPA 3.13 execution. Never prepares, pulls or chooses a fallback."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from refinery import gepa_runtime as runtime  # noqa: E402


def bounded(
    argv: list[str], timeout: int, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    streams: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("GEPA command pipes absent")
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("GEPA command deadline")
                for key, _ in selector.select(min(remaining, 0.25)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    streams[key.data].extend(data)
                    if sum(map(len, streams.values())) > 1024 * 1024:
                        raise ValueError("GEPA command output bound")
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(
            argv, code, streams["stdout"].decode(), streams["stderr"].decode()
        )
    finally:
        cleanup_error: BaseException | None = None
        try:
            if process.poll() is None:
                process.kill()
        except BaseException as exc:
            cleanup_error = exc
        try:
            process.wait(timeout=5)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if cleanup_error is not None:
            raise cleanup_error


GIT_SHA256 = "54af380ba6ca1b36305358e99427a31ae4b0af5dc5cb6d0198c6f2f16e97651d"


def canonical_input(value: str, *, directory: bool) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or value != str(path.resolve(strict=True))
        or any(c in value for c in (":", ",", "\n"))
    ):
        raise ValueError("canonical materialized input path required")
    mode = path.lstat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError("regular materialized input required")
    return path


def materialized_inputs(tether: str, git: str) -> dict[str, Any]:
    from refinery.tether_dependency import COMMIT, bounded_file

    source = canonical_input(tether, directory=True)
    binary = canonical_input(git, directory=False)
    info = binary.stat()
    if (
        stat.S_IMODE(info.st_mode) != 0o755
        or runtime.digest(bounded_file(binary, 16 * 1024 * 1024)) != GIT_SHA256
    ):
        raise ValueError("pinned real Git binary changed")
    pin_raw = runtime.source_bytes("dependencies/tether-feedback.json")
    pin = json.loads(pin_raw)
    if pin["commit"] != COMMIT:
        raise ValueError("current Tether pin required")
    actual = {}
    for name, expected in pin["files"].items():
        file = canonical_input(str(source / name), directory=False)
        actual[name] = runtime.digest(bounded_file(file))
        if actual[name] != expected:
            raise ValueError("Tether source file changed")
    return {
        "git_sha256": GIT_SHA256,
        "tether_commit": COMMIT,
        "tether_pin_sha256": runtime.digest(pin_raw),
        "tether_files": actual,
    }


def execute(
    prepared: str,
    *,
    suite: str = "reflection",
    tether: str | None = None,
    git: str | None = None,
) -> dict[str, Any]:
    if suite not in {"reflection", "durable", "materialized"}:
        raise ValueError("unknown GEPA suite")
    inputs = None
    if suite == "materialized":
        if type(tether) is not str or type(git) is not str:
            raise ValueError("explicit materialized Tether and Git inputs required")
        inputs = materialized_inputs(tether, git)
    elif tether is not None or git is not None:
        raise ValueError("materialized inputs cannot alter legacy lanes")
    base = Path(prepared).absolute()
    if any(c in str(path) for path in (base, ROOT) for c in (":", ",", "\n")):
        raise ValueError("unsafe GEPA mount path")
    runtime.verify_prepared(base)
    from tools.test_gepa_reflection import source_hashes, validate

    if suite == "durable":
        from tools.test_gepa_durable import source_hashes, validate_report
    if suite == "materialized":
        from tools.test_gepa_materialized import source_hashes, validate_report
    sources = source_hashes()
    inspected = bounded(["docker", "image", "inspect", runtime.IMAGE], 30)
    if inspected.returncode:
        raise ValueError("pinned GEPA image unavailable")
    images = json.loads(inspected.stdout)
    runtime.verify_image(images)
    container = "runspindle-gepa-" + uuid.uuid4().hex
    argv = [
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
    ]
    if suite == "materialized":
        argv += [
            "-v",
            str(tether) + ":/tether:ro",
            "-v",
            str(git) + ":/usr/bin/git:ro",
            "-e",
            "RUNSPINDLE_TETHER_SOURCE=/tether",
        ]
    for entry in (
        "PYTHONPATH=/deps:/work",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONHASHSEED=0",
        "HOME=/tmp",
        "DSPY_CACHEDIR=/tmp/dspy",
        "HF_HUB_OFFLINE=1",
        "LITELLM_LOCAL_MODEL_COST_MAP=True",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "LC_ALL=C",
        "TZ=UTC",
    ):
        argv += ["-e", entry]
    argv += [
        runtime.IMAGE,
        "/usr/local/bin/python3.13",
        "tools/test_gepa_reflection.py"
        if suite == "reflection"
        else "tools/test_gepa_durable.py"
        if suite == "durable"
        else "tools/test_gepa_materialized.py",
    ]
    try:
        result = bounded(argv, 120)
    finally:
        # --rm may already have removed the container. Only a successful scoped
        # listing proves absence; daemon/transport errors are never absence.
        try:
            bounded(["docker", "rm", "--force", container], 30)
        finally:
            absence = bounded(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    "name=^/" + container + "$",
                    "--format",
                    "{{.ID}}",
                ],
                30,
            )
            if absence.returncode != 0 or absence.stdout.strip():
                raise RuntimeError("GEPA container absence not established")
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("GEPA native qualification failed")
    value = json.loads(result.stdout)
    if suite in {"durable", "materialized"}:
        validate_report(value)
        if suite == "materialized" and (
            value["inputs"] != inputs
            or materialized_inputs(cast(str, tether), cast(str, git)) != inputs
        ):
            raise ValueError("materialized execution inputs changed")
        if source_hashes() != sources:
            raise ValueError("GEPA source changed during execution")
        return cast(dict[str, Any], value)
    if (
        type(value) is not dict
        or value.get("status") != "passed"
        or value.get("schema") != "runspindle.gepa313-native.v1"
        or value.get("sources") != sources
        or value.get("adoption_sha256") != runtime.ADOPTION_SHA256
        or value.get("repeat_equal") is not True
        or type(value.get("runs")) is not list
        or len(value["runs"]) != 2
        or value["runs"][0] != value["runs"][1]
    ):
        raise ValueError("GEPA report missing success")
    for run in value["runs"]:
        validate(run)
    if source_hashes() != sources:
        raise ValueError("GEPA source changed during execution")
    return cast(dict[str, Any], value)


def main() -> None:
    def interrupted(*_: Any) -> None:
        raise KeyboardInterrupt("GEPA lane cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--suite",
        choices=("reflection", "durable", "materialized"),
        default="reflection",
    )
    parser.add_argument("--tether")
    parser.add_argument("--git")
    args = parser.parse_args()
    # Refuse overwrite before any runtime execution. No auto-acceptance of evidence.
    with args.report.open("x") as output:
        report = execute(
            args.prepared, suite=args.suite, tether=args.tether, git=args.git
        )
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
