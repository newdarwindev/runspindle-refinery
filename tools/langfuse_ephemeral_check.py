#!/usr/bin/env python3
"""Opt-in synthetic score workflow against an externally provisioned TLS server.

The runner owns server image evidence, credentials, lifecycle and cleanup. This
script never starts a service or asserts that a protocol-compatible mock is real
Langfuse. No input/output text is exported: only existing opaque score metadata.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from refinery.feedback_projection import ProjectionLedger  # noqa: E402
from refinery.langfuse_http import LangfuseHTTP  # noqa: E402
from refinery.langfuse_projection import LangfuseProjection  # noqa: E402


def read_regular(path: Path, bound: int, *, private: bool) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_nlink != 1
            or meta.st_size > bound
            or (private and stat.S_IMODE(meta.st_mode) != 0o600)
        ):
            raise ValueError("bounded regular private configuration required")
        data = os.read(fd, bound + 1)
        after = os.fstat(fd)
        if len(data) > bound or (
            meta.st_dev,
            meta.st_ino,
            meta.st_size,
            meta.st_mtime_ns,
            meta.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("configuration changed while reading")
        return data
    finally:
        os.close(fd)


def configuration(path: Path) -> dict[str, Any]:
    value = json.loads(read_regular(path, 65536, private=True))
    if type(value) is not dict or set(value) != {
        "schema",
        "origin",
        "authorization_file",
        "project_id",
        "trace_id",
        "ca_file",
        "ca_sha256",
        "backend",
    }:
        raise ValueError("exact ephemeral runner configuration required")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("runner configuration schema mismatch")
    if any(
        type(value[k]) is not str or not 1 <= len(value[k]) <= 4096
        for k in (
            "origin",
            "authorization_file",
            "project_id",
            "trace_id",
            "ca_file",
            "ca_sha256",
        )
    ):
        raise ValueError("explicit runner configuration required")
    backend = value["backend"]
    if type(backend) is not dict or set(backend) != {
        "manifest_sha256",
        "config_sha256",
        "version",
    }:
        raise ValueError("exact backend descriptor required")
    if type(backend["version"]) is not str or not 1 <= len(backend["version"]) <= 64:
        raise ValueError("bounded backend version required")
    for digest in (
        value["ca_sha256"],
        backend["manifest_sha256"],
        backend["config_sha256"],
    ):
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("pinned digest required")
    ca = read_regular(Path(value["ca_file"]), 32768, private=False)
    if hashlib.sha256(ca).hexdigest() != value["ca_sha256"]:
        raise ValueError("test CA pin mismatch")
    authorization = read_regular(
        Path(value["authorization_file"]), 4096, private=True
    ).decode("ascii")
    return dict(value, ca_pem=ca.decode("ascii"), authorization=authorization)


class ObservedHTTP:
    """Transparent real HTTP observation, with a named post-acceptance fault."""

    def __init__(self, inner: LangfuseHTTP, deadline: float) -> None:
        self.inner = inner
        self.origin = inner.origin
        self.deadline = deadline
        self.posts = 0
        self.gets = 0
        self.lose_ack = False

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout: int,
        max_bytes: int,
    ) -> Any:
        remaining = int(self.deadline - time.monotonic())
        if remaining < 1:
            raise TimeoutError("ephemeral workflow deadline exceeded")
        if method == "POST":
            self.posts += 1
        else:
            self.gets += 1
        result = self.inner.request(
            method, path, body, timeout=min(timeout, remaining), max_bytes=max_bytes
        )
        if method == "POST" and self.lose_ack:
            self.lose_ack = False
            if result.status != 200:
                raise ValueError("lost-ack challenge requires actual POST acceptance")
            raise TimeoutError("controlled loss after actual HTTP acceptance")
        return result


def qualify(config: dict[str, Any]) -> dict[str, Any]:
    start = time.monotonic()
    http = ObservedHTTP(
        LangfuseHTTP(config["origin"], config["authorization"], config["ca_pem"]),
        start + 90,
    )
    # Existing synthetic fixture uses actual signed Tether admission and immutable
    # lineage, not fabricated projection authority. Its fake HTTP is never used.
    fixture = importlib.import_module("test_feedback_projection").ProjectionTests()
    fixture.setUp()
    ledger = None
    try:
        fixture.adapter = LangfuseProjection(
            http, config["project_id"], config["trace_id"], config["origin"]
        )
        path = fixture.h.root / "actual-server-outbox.sqlite"
        ledger = ProjectionLedger(path, fixture.adapter.destination)

        def settle() -> dict[str, Any]:
            if ledger is None:
                raise ValueError("local ledger unavailable")
            for _ in range(15):
                status = ledger.dispatch(fixture.source, fixture.adapter)
                if status == "delivered":
                    return ledger.capture(fixture.source)
                if status not in {"uncertain"} or time.monotonic() >= http.deadline - 1:
                    raise ValueError("actual score readback did not settle")
                time.sleep(0.2)
            raise ValueError("actual score readback attempt bound exceeded")

        active = settle()
        if http.posts != 1 or active["gold"] is not True:
            raise ValueError("initial actual score effect mismatch")
        fixture.authority["revision"] = "lost-ack-policy"
        http.lose_ack = True
        try:
            ledger.dispatch(fixture.source, fixture.adapter)
        except TimeoutError as exc:
            if str(exc) != "controlled loss after actual HTTP acceptance":
                raise
        else:
            raise ValueError("lost-ack fault did not fire")
        ledger.close()
        ledger = ProjectionLedger(path, fixture.adapter.destination)
        recovered = settle()
        if http.posts != 2 or recovered["projection_id"] == active["projection_id"]:
            raise ValueError("ambiguous effect was repeated or identity unchanged")
        fixture.authority["export_allowed"] = False
        if (
            ledger.dispatch(fixture.source, fixture.adapter) != "blocked"
            or http.posts != 2
        ):
            raise ValueError("withdrawn export caused remote effect")
        fixture.authority["export_allowed"] = True
        raw = fixture.h.reaction(removed=True)
        fixture.h.app.react(raw, fixture.h.headers(raw))
        revoked = settle()
        if (
            http.posts != 3
            or revoked["state"] != "revoked"
            or revoked["gold"] is not False
        ):
            raise ValueError("revocation projection mismatch")
        return dict(
            version="refinery.langfuse-ephemeral-score-check.v1",
            scope="synthetic_metadata_only_external_server_candidate",
            backend_descriptor=config["backend"],
            ca_sha256=config["ca_sha256"],
            source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            source_files={
                str(path.relative_to(ROOT)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted((ROOT / "refinery").glob("*.py"))
            },
            active_id=active["projection_id"],
            recovered_id=recovered["projection_id"],
            revoked_id=revoked["projection_id"],
            actual_post_entries=http.posts,
            actual_get_entries=http.gets,
            lost_ack="injected_after_actual_POST_acceptance",
            restart="local_ledger_reopened_not_server_restart",
            no_blind_repost=True,
            revoked_gold=False,
            server_identity="runner_evidence_required_not_inferred_from_HTTP",
        )
    finally:
        if ledger is not None:
            ledger.close()
        fixture.doCleanups()


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    output = Path(sys.argv[2])
    if output.exists() or output.is_symlink():
        return 2
    try:
        result = qualify(configuration(Path(sys.argv[1])))
        with output.open("x") as stream:
            json.dump(result, stream, sort_keys=True, separators=(",", ":"))
    except Exception:
        # Neither server responses nor private configuration enter diagnostics.
        print(
            "ephemeral score qualification failed; no success receipt", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
