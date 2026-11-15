"""Bounded opt-in launcher. Requires external approval of the exact image/plan.

No import-time execution, alternate daemon, setup, package fetch or removal.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from tools.dspy_x64_container_contract import (
    check_observation,
    check_result,
    create_spec,
)


class Stream(Protocol):
    def read(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class Backend(Protocol):
    def create(self, spec: dict[str, Any]) -> str: ...
    def inspect(self, identity: str) -> dict[str, Any]: ...
    def start(self, identity: str) -> None: ...
    def signal(self, identity: str, signal: str) -> None: ...
    def logs(self, identity: str) -> Stream: ...
    def cgroup(self, pid: int, identity: str) -> str: ...
    def populated(self, token: str) -> bool | None: ...
    def cgroup_evidence(self, token: str) -> dict[str, Any]: ...


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, timeout: float = 3.0):
        super().__init__("localhost", timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


class DockerStream:
    def __init__(self, connection: UnixConnection, response: http.client.HTTPResponse):
        self.connection, self.response = connection, response

    def read(self, size: int) -> bytes:
        return self.response.read(size)

    def close(self) -> None:
        self.response.close()
        self.connection.close()


class Docker:
    def __init__(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        self.group_evidence: dict[str, dict[str, Any]] = {}

    def request(self, method: str, path: str, body: Any = None) -> Any:
        connection = UnixConnection()
        try:
            data = json.dumps(body).encode() if body is not None else None
            connection.request(
                method, "/v1.51" + path, data, {"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            data = response.read(16777217)
            if response.status not in (200, 201, 204):
                raise RuntimeError(
                    "Docker API refused request: " + str(response.status)
                )
            if len(data) > 16777216:
                raise RuntimeError("Docker response limit")
            return json.loads(data) if data else None
        finally:
            connection.close()

    def create(self, spec: dict[str, Any]) -> str:
        result = self.request("POST", "/containers/create", spec)
        identity: str = result["Id"]
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise RuntimeError("unexpected container identity")
        if result.get("Warnings"):
            raise RuntimeError(
                "created but unstarted container retained; warnings refused: "
                + identity
            )
        return identity

    def inspect(self, identity: str) -> dict[str, Any]:
        result: dict[str, Any] = self.request("GET", f"/containers/{identity}/json")
        return result

    def start(self, identity: str) -> None:
        self.request("POST", f"/containers/{identity}/start")

    def signal(self, identity: str, signal: str) -> None:
        if signal not in {"SIGUSR1", "SIGKILL"}:
            raise ValueError("unsupported bounded control signal")
        self.request("POST", f"/containers/{identity}/kill?signal={signal}")

    def logs(self, identity: str) -> Stream:
        connection = UnixConnection(timeout=125)
        connection.request(
            "GET", f"/v1.51/containers/{identity}/logs?stdout=1&stderr=1&follow=1"
        )
        response = connection.getresponse()
        if response.status != 200:
            connection.close()
            raise RuntimeError("Docker log access refused: " + str(response.status))
        return DockerStream(connection, response)

    @staticmethod
    def pid_start(pid: int) -> int:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return int(fields[19])

    @staticmethod
    def read_at(descriptor: int, name: str) -> str:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            data = os.read(fd, 1048577)
            if len(data) > 1048576:
                raise RuntimeError("cgroup observation size bound")
            return data.decode()
        finally:
            os.close(fd)

    def cgroup(self, pid: int, identity: str) -> str:
        start = self.pid_start(pid)
        rows = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        if len(rows) != 1 or not rows[0].startswith("0::/"):
            raise RuntimeError("one cgroup-v2 membership required")
        relative = rows[0][4:]
        if ".." in relative.split("/") or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise RuntimeError("unsafe cgroup/container identity")
        path = Path("/sys/fs/cgroup") / relative
        if path.name != f"docker-{identity}.scope":
            raise RuntimeError("cgroup identity differs from created container")
        fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        fields = dict(
            line.split(":", 1)
            for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
            if ":" in line
        )
        mount_id = int(fields["mnt_id"].strip())
        mounts = [
            line.split(" - ", 1)
            for line in Path("/proc/self/mountinfo").read_text().splitlines()
        ]
        matching = [row for row in mounts if int(row[0].split()[0]) == mount_id]
        device = f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}"
        if (
            len(matching) != 1
            or matching[0][1].split()[0] != "cgroup2"
            or matching[0][0].split()[2] != device
        ):
            raise RuntimeError("held directory is not on observed cgroup2 mount")
        limits = {
            "pids": int(self.read_at(fd, "pids.max").strip()),
            "memory": int(self.read_at(fd, "memory.max").strip()),
            "swap": int(self.read_at(fd, "memory.swap.max").strip()),
        }
        quota, period = self.read_at(fd, "cpu.max").split()
        limits.update(cpu_quota=int(quota), cpu_period=int(period))
        if (
            limits["pids"] != 64
            or limits["memory"] != 2147483648
            or limits["swap"] != 0
            or limits["cpu_quota"] != limits["cpu_period"]
            or limits["cpu_period"] <= 0
        ):
            raise RuntimeError("actual cgroup resource limits differ")
        observed = self.inspect(identity)
        if (
            observed.get("Id") != identity
            or not observed["State"]["Running"]
            or observed["State"]["Pid"] != pid
            or self.pid_start(pid) != start
        ):
            raise RuntimeError("Engine PID changed or host PID reused")
        token = str(path)
        self.groups[token] = {
            "fd": fd,
            "container_id": identity,
            "host_pid": pid,
            "pid_starttime_ticks": start,
            "pid_starttime_rechecked": start,
            "device": info.st_dev,
            "inode": info.st_ino,
            "mount_id": mount_id,
            "mount_fstype": "cgroup2",
            "limits": limits,
            "initial_populated": None,
            "descriptor_held_before_release": True,
        }
        return token

    def populated(self, token: str) -> bool | None:
        group = self.groups[token]
        fd = group["fd"]
        held = os.fstat(fd)
        if (held.st_dev, held.st_ino) != (
            group["device"],
            group["inode"],
        ) or not stat.S_ISDIR(held.st_mode):
            raise RuntimeError("held cgroup inode changed")
        base = {key: value for key, value in group.items() if key != "fd"}
        base.update(
            held_device=held.st_dev,
            held_inode=held.st_ino,
            held_mode=stat.S_IFMT(held.st_mode),
            held_nlink=held.st_nlink,
        )
        if held.st_nlink == 0:
            self.group_evidence[token] = {
                **base,
                "kind": "tracked_cgroup_inode_removed",
            }
            return None  # Distinct proof kind; never fabricate populated=0.
        try:
            rows = dict(
                line.split() for line in self.read_at(fd, "cgroup.events").splitlines()
            )
        except FileNotFoundError:
            held = os.fstat(fd)
            base.update(
                held_device=held.st_dev,
                held_inode=held.st_ino,
                held_mode=stat.S_IFMT(held.st_mode),
                held_nlink=held.st_nlink,
            )
            if (
                held.st_nlink == 0
                and (held.st_dev, held.st_ino) == (group["device"], group["inode"])
                and stat.S_ISDIR(held.st_mode)
            ):
                self.group_evidence[token] = {
                    **base,
                    "kind": "tracked_cgroup_inode_removed",
                }
            else:
                self.group_evidence[token] = {**base, "kind": "unknown"}
            return None
        if rows.get("populated") not in {"0", "1"}:
            raise RuntimeError("cgroup population observation missing")
        if group["initial_populated"] is None and rows["populated"] == "1":
            group["initial_populated"] = 1
            base["initial_populated"] = 1
        self.group_evidence[token] = {
            **base,
            "kind": "cgroup.events",
            "populated": int(rows["populated"]),
        }
        return rows["populated"] == "1"

    def cgroup_evidence(self, token: str) -> dict[str, Any]:
        return dict(self.group_evidence[token])


def capture(
    stream: Stream,
    directory: Path,
    stopped: threading.Event,
    result: dict[str, Any],
    contract_sha256: str,
) -> None:
    """Retain every received byte, including over-limit and shutdown bytes."""
    try:
        with (
            (directory / "docker-stream.bin").open("xb") as raw,
            (directory / "stdout").open("xb") as stdout,
            (directory / "stderr").open("xb") as stderr,
        ):
            counts = {1: 0, 2: 0}
            first_line = bytearray()

            def read(size: int) -> bytes:
                data = stream.read(size)
                raw.write(data)
                return data

            while True:
                header = read(8)
                if not header:
                    result["drained"] = True
                    break
                if (
                    len(header) != 8
                    or header[0] not in (1, 2)
                    or header[1:4] != b"\0\0\0"
                ):
                    raise RuntimeError("malformed Docker stream header")
                remaining = struct.unpack(">I", header[4:])[0]
                target = stdout if header[0] == 1 else stderr
                while remaining:
                    block = read(min(remaining, 65536))
                    if not block:
                        raise RuntimeError("incomplete Docker stream payload")
                    target.write(block)
                    if (
                        header[0] == 1
                        and not result.get("ready")
                        and len(first_line) <= 1048576
                    ):
                        first_line.extend(block)
                        if b"\n" in first_line:
                            line = bytes(first_line).split(b"\n", 1)[0]
                            if json.loads(line) != {
                                "control": "ready",
                                "contract_sha256": contract_sha256,
                            }:
                                raise RuntimeError("readiness contract differs")
                            result["ready"] = True
                    remaining -= len(block)
                    counts[header[0]] += len(block)
                    if counts[header[0]] > 1048576:
                        result["output_stop"] = True
                        stopped.set()
            result["counts"] = counts
    except Exception as error:
        result["capture_error"] = type(error).__name__
        stopped.set()
    finally:
        stream.close()


def launch(spec: dict[str, Any], backend: Backend, directory: Path) -> dict[str, Any]:
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    started = time.monotonic()
    identity: str | None = None
    may_be_running = False
    kill_sent = False
    thread: threading.Thread | None = None
    capture_result: dict[str, Any] = {"drained": False, "output_stop": False}
    receipt: dict[str, Any] = {
        "classification": "actual_attempt_not_qualification",
        "spec": spec,
    }

    def kill() -> None:
        nonlocal kill_sent
        if identity is not None and may_be_running and not kill_sent:
            kill_sent = True
            backend.signal(identity, "SIGKILL")

    def capture_backend(identity: str, event: threading.Event) -> None:
        # Header/body waits happen off the controlling thread, so a stalled
        # log API cannot prevent its wall watchdog and same-route stop.
        try:
            capture(
                backend.logs(identity), directory, event, capture_result, spec["Cmd"][1]
            )
        except Exception as error:
            capture_result["capture_error"] = type(error).__name__
            capture_result["capture_reason"] = str(error)
            event.set()

    try:
        identity = backend.create(spec)
        receipt["container_id"] = identity
        observed = backend.inspect(identity)
        if observed.get("Id") != identity:
            raise RuntimeError("Engine container identity differs")
        check_observation(spec, observed)
        may_be_running = True
        backend.start(identity)
        observed = backend.inspect(identity)
        if observed.get("Id") != identity:
            raise RuntimeError("Engine container identity differs")
        check_observation(spec, observed)
        pid = observed["State"]["Pid"]
        if not observed["State"]["Running"] or type(pid) is not int or pid <= 0:
            raise RuntimeError("readiness-gated process missing")
        token = backend.cgroup(pid, identity)
        receipt["cgroup"] = token
        if backend.populated(token) is not True:
            raise RuntimeError("initial populated cgroup not observed")
        event = threading.Event()
        thread = threading.Thread(
            target=capture_backend,
            args=(identity, event),
            daemon=True,
        )
        thread.start()
        while not capture_result.get("ready"):
            if (
                event.wait(0.05)
                or not thread.is_alive()
                or time.monotonic() - started > 120
            ):
                kill()
                raise RuntimeError("exact readiness not observed within bounds")
        if time.monotonic() - started > 120:
            raise RuntimeError("readiness exceeded wall bound")
        backend.signal(identity, "SIGUSR1")
        deadline_stop = False
        while thread.is_alive():
            if event.wait(0.05) or time.monotonic() - started > 120:
                deadline_stop = time.monotonic() - started > 120
                kill()
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError("output draining unresolved; container retained")
                break
        observed = backend.inspect(identity)
        if observed.get("Id") != identity:
            raise RuntimeError("terminal Engine identity differs")
        if observed["State"]["Running"]:
            kill()
            raise RuntimeError("container still running after output EOF")
        may_be_running = False
        populated = backend.populated(token)
        receipt.update(
            {
                "observed": observed,
                "capture": capture_result,
                "elapsed_seconds": time.monotonic() - started,
                "cgroup_populated": populated,
                "cgroup_evidence": backend.cgroup_evidence(token),
            }
        )
        result = check_result(
            spec,
            observed,
            (directory / "stdout").read_bytes(),
            (directory / "stderr").read_bytes(),
            elapsed_seconds=time.monotonic() - started,
            deadline_or_output_stop=deadline_stop
            or bool(capture_result["output_stop"]),
            streams_fully_drained=bool(capture_result["drained"])
            and "capture_error" not in capture_result,
            cgroup_populated=populated,
            cgroup_proof=backend.cgroup_evidence(token),
        )
        receipt["result"] = result
        return result
    except Exception as error:
        receipt["failure"] = type(error).__name__ + ": " + str(error)
        try:
            kill()
        except Exception as cleanup_error:
            receipt["cleanup_error"] = (
                type(cleanup_error).__name__ + ": " + str(cleanup_error)
            )
        if thread is not None:
            thread.join(timeout=5)
        raise
    finally:
        receipt["capture"] = capture_result
        receipt["kill_sent"] = kill_sent
        with (directory / "launcher-receipt.json").open("x") as output:
            output.write(json.dumps(receipt, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            launch(
                create_spec(args.image, args.contract_sha256, args.source_sha),
                Docker(),
                args.evidence,
            )
        )
    )


if __name__ == "__main__":
    main()
