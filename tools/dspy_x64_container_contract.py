"""Inert opt-in container contract; construction is not execution authority."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, TypedDict

SCHEMA = "runspindle.dspy-x64-container-smoke.v1"


class Fixture(TypedDict):
    answer: str
    tokens: int
    cost_microunits: int


FIXTURE: Fixture = {
    "answer": "synthetic-container-smoke",
    "tokens": 1,
    "cost_microunits": 0,
}
ENVIRONMENT = {
    "PATH": "/runtime/python/bin",
    "HOME": "/scratch",
    "TMPDIR": "/scratch",
    "LC_ALL": "C",
    "TZ": "UTC",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "DSPY_CACHEDIR": "/scratch/dspy-cache",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("exact SHA256 required")
    return value


def create_spec(image: str, contract_sha256: str, source_sha: str) -> dict[str, Any]:
    """Exact Engine create body, not a call to Docker or a permission grant."""
    if not image.startswith("sha256:"):
        raise ValueError("immutable image ID required")
    sha256(image.removeprefix("sha256:"))
    sha256(contract_sha256)
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("exact source revision required")
    return {
        "Image": image,
        "User": "65532:65532",
        "WorkingDir": "/scratch",
        "Entrypoint": [
            "/runtime/python/bin/python3.14",
            "-I",
            "-S",
            "-B",
            "/source/tools/dspy_x64_container_smoke.py",
        ],
        "Cmd": ["--contract-sha256", contract_sha256],
        "Env": [f"{key}={value}" for key, value in ENVIRONMENT.items()],
        "NetworkDisabled": True,
        "Labels": {
            "runspindle.source_sha": source_sha,
            "runspindle.contract_sha256": contract_sha256,
            "runspindle.classification": "unreviewed_candidate_not_execution_authority",
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64,
            "Memory": 2147483648,
            "MemorySwap": 2147483648,
            "NanoCpus": 1000000000,
            "Privileged": False,
            "Init": False,
            "Binds": [],
            "Mounts": [],
            "Devices": [],
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "CgroupnsMode": "private",
            "UsernsMode": "",
            "Runtime": "runc",
            "RestartPolicy": {"Name": "no"},
            "AutoRemove": False,
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "-1"}},
            "Tmpfs": {
                "/scratch": "rw,noexec,nosuid,nodev,size=134217728,mode=0700,uid=65532,gid=65532"
            },
            "Ulimits": [
                {"Name": "cpu", "Soft": 60, "Hard": 60},
                {"Name": "nofile", "Soft": 64, "Hard": 64},
                {"Name": "core", "Soft": 0, "Hard": 0},
                {"Name": "fsize", "Soft": 8388608, "Hard": 8388608},
            ],
        },
    }


def check_observation(spec: dict[str, Any], observed: dict[str, Any]) -> None:
    """Require reviewed settings before start, without changing any settings."""
    config, host = observed["Config"], observed["HostConfig"]
    if observed.get("AppArmorProfile") != "docker-default":
        raise ValueError("existing default AppArmor profile required")
    if observed["Image"] != spec["Image"] or observed.get("Mounts"):
        raise ValueError("image or mounts differ")
    for field in (
        "User",
        "WorkingDir",
        "Entrypoint",
        "Cmd",
        "NetworkDisabled",
        "Labels",
    ):
        if config.get(field) != spec[field]:
            raise ValueError("container configuration differs: " + field)
    if sorted(config.get("Env", [])) != sorted(spec["Env"]):
        raise ValueError("container environment differs")
    for field, value in spec["HostConfig"].items():
        if host.get(field) != value:
            raise ValueError("container confinement differs: " + field)


def check_result(
    spec: dict[str, Any],
    observed: dict[str, Any],
    stdout: bytes,
    stderr: bytes,
    *,
    elapsed_seconds: float,
    deadline_or_output_stop: bool,
    streams_fully_drained: bool,
    cgroup_populated: bool | None,
) -> dict[str, Any]:
    """Fail closed on incomplete externally observed output/descendant evidence.

    The owner-reviewed launcher must collect these actual observations. This
    inert validator performs no execution, cleanup, resource or authority change.
    """
    check_observation(spec, observed)
    state = observed["State"]
    if (
        not 0 <= elapsed_seconds <= 120
        or deadline_or_output_stop
        or not streams_fully_drained
        or cgroup_populated is not False
        or len(stdout) > 1048576
        or len(stderr) > 1048576
        or state.get("Running") is not False
        or state.get("Pid") != 0
        or state.get("ExitCode") != 0
        or state.get("OOMKilled") is not False
        or state.get("Error")
    ):
        raise ValueError("incomplete or failed bounded execution evidence")
    result: dict[str, Any] = json.loads(stdout)
    if (
        result.get("status") != "passed"
        or result.get("evidence_class") != "actual_framework_synthetic_completion_smoke"
        or result.get("candidate_sha256")
        != "d1660d0927cd2b325a50b426af7cd608311dafa367dd453f51d21418a7ad285f"
        or result.get("source_sha") != spec["Labels"]["runspindle.source_sha"]
        or result.get("actual_framework") != "DSPy 3.1.0"
        or result.get("quality") != "synthetic_only"
        or result.get("provider_calls") != 1
        or result.get("lm_calls") != 1
        or result.get("bootstrap") != "not_run"
        or result.get("restart") != "not_run"
        or result.get("live_provider") != "not_run"
        or result.get("serving") != "not_run"
    ):
        raise ValueError("actual smoke receipt differs")
    return result
