#!/usr/bin/env python3
"""Source-bound synthetic dataset client; externally supervised runner owns server."""

from __future__ import annotations

import hashlib
import importlib
import ipaddress
import json
import os
import platform
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from refinery import dataset_contract as c  # noqa: E402
from refinery.dataset_export import DatasetProjection, Destination, Export  # noqa: E402
from refinery.evaluation import Halt  # noqa: E402
from refinery.langfuse_http import DatasetHTTP, validate_origin  # noqa: E402
from refinery.storage import Store  # noqa: E402
from tools.langfuse_ephemeral_check import read_regular  # noqa: E402

BACKEND = dict(
    manifest_sha256="ea4f29781be662f8c0b15c5c5a004f5352d3b769b8f143fa6556dd20a717ce76",
    config_sha256="b7123f7d32f06e4a01c42795ebc331abb2493ccd09b90bcf9e9ea7ea54c612b2",
    version="4.82.0",
)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate configuration field")
        value[key] = item
    return value


def regular(path: Path, bound: int, *, private: bool) -> bytes:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("canonical absolute configuration path required")
    before = path.lstat()
    if before.st_uid != os.geteuid():
        raise ValueError("owned configuration required")
    raw = read_regular(path, bound, private=private)
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("configuration path changed")
    return raw


def configuration(path: Path) -> dict[str, Any]:
    value = json.loads(regular(path, 65536, private=True), object_pairs_hook=unique)
    if type(value) is not dict or set(value) != {
        "schema",
        "origin",
        "authorization_file",
        "project_id",
        "dataset_id",
        "dataset_name",
        "dataset_split",
        "ca_file",
        "ca_sha256",
        "backend",
    }:
        raise ValueError("exact dataset runner configuration required")
    if (
        type(value["schema"]) is not int
        or value["schema"] != 1
        or value["backend"] != BACKEND
        or type(value["backend"]) is not dict
    ):
        raise ValueError("exact approved backend/schema required")
    for key in (
        "origin",
        "authorization_file",
        "project_id",
        "dataset_id",
        "dataset_name",
        "dataset_split",
        "ca_file",
        "ca_sha256",
    ):
        if type(value[key]) is not str or not 1 <= len(value[key]) <= 4096:
            raise ValueError("bounded configuration string required")
    validate_origin(value["origin"], None)
    if not ipaddress.ip_address(urlsplit(value["origin"]).hostname or "").is_loopback:
        raise ValueError("loopback-only qualification required")
    Destination(
        value["origin"],
        value["project_id"],
        value["dataset_id"],
        value["dataset_name"],
        value["dataset_split"],
    )
    if value["dataset_split"] != "train":
        raise ValueError("signed synthetic fixture requires train dataset")
    ca = regular(Path(value["ca_file"]), 32768, private=False)
    if hashlib.sha256(ca).hexdigest() != value["ca_sha256"]:
        raise ValueError("exact CA hash required")
    auth = regular(Path(value["authorization_file"]), 4096, private=True).decode(
        "ascii"
    )
    DatasetHTTP(value["origin"], auth, ca.decode("ascii"))
    return dict(value, ca_pem=ca.decode("ascii"), authorization=auth)


class ObservedHTTP:
    """Transparent actual transport plus one post-acceptance lost-ack challenge."""

    def __init__(self, inner: DatasetHTTP, deadline: float):
        self.inner, self.deadline = inner, deadline
        self.origin = inner.origin
        self.posts = 0
        self.gets = 0
        self.accepted = 0
        self.faults = 0
        self.calls: list[dict[str, Any]] = []

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
            raise TimeoutError("dataset workflow deadline exceeded")
        if method == "POST":
            self.posts += 1
            if self.posts != 1:
                raise ValueError("repeat POST forbidden in qualification")
        else:
            self.gets += 1
            if self.gets > 32:
                raise ValueError("readback count bound")
        result = self.inner.request(
            method, path, body, timeout=min(timeout, remaining), max_bytes=max_bytes
        )
        self.calls.append(
            dict(
                method=method,
                endpoint_sha256=c.digest(path.encode()),
                body_sha256=None if body is None else c.digest(body),
                status=result.status,
                response_sha256=c.digest(result.body),
            )
        )
        if method == "POST":
            answer = json.loads(result.body, object_pairs_hook=unique)
            expected = json.loads(body or b"{}")
            if (
                result.status != 200
                or type(answer) is not dict
                or answer.get("id") != expected["id"]
            ):
                raise ValueError("actual POST acceptance required before fault")
            self.accepted += 1
            self.faults += 1
            raise TimeoutError("controlled loss after actual dataset acceptance")
        return result


FAILURE_STAGES = frozenset(
    {
        "fixture_setup",
        "fixture_admission",
        "initial_export",
        "reopen_readback",
        "current_grant_denial",
        "source_withdrawal",
        "registry_withdrawal",
        "local_revocation",
        "receipt_binding",
    }
)


class QualificationFailure(ValueError):
    def __init__(self, stage: str, error: Exception, http: ObservedHTTP):
        super().__init__("dataset qualification failed")
        allowed = {"ValueError", "TimeoutError", "OSError", "UseDenied"}
        self.receipt = dict(
            qualification="failed",
            stage=stage if stage in FAILURE_STAGES else "unknown_stage",
            error_class=type(error).__name__
            if type(error).__name__ in allowed
            else "other",
            post_entries=http.posts,
            get_entries=http.gets,
            accepted_posts=http.accepted,
            injected_lost_ack=http.faults,
            last_status=(
                http.calls[-1]["status"]
                if http.calls
                and type(http.calls[-1]["status"]) is int
                and 100 <= http.calls[-1]["status"] <= 599
                else None
            ),
            source_sha256=c.digest(Path(__file__).read_bytes()),
        )


def validate_context(config: dict[str, Any]) -> None:
    if type(config) is not dict or set(config) != {
        "schema",
        "origin",
        "authorization_file",
        "project_id",
        "dataset_id",
        "dataset_name",
        "dataset_split",
        "ca_file",
        "ca_sha256",
        "backend",
        "ca_pem",
        "authorization",
    }:
        raise ValueError("exact loaded qualification context required")
    if (
        type(config["schema"]) is not int
        or config["schema"] != 1
        or type(config["backend"]) is not dict
        or config["backend"] != BACKEND
        or config["dataset_split"] != "train"
    ):
        raise ValueError("pinned synthetic dataset context required")
    for key in (
        "origin",
        "project_id",
        "dataset_id",
        "dataset_name",
        "dataset_split",
        "ca_sha256",
        "authorization",
        "ca_pem",
    ):
        if type(config[key]) is not str:
            raise ValueError("explicit qualification strings required")
    validate_origin(config["origin"], None)
    if not ipaddress.ip_address(urlsplit(config["origin"]).hostname or "").is_loopback:
        raise ValueError("loopback-only qualification required")
    Destination(
        config["origin"],
        config["project_id"],
        config["dataset_id"],
        config["dataset_name"],
        config["dataset_split"],
    )
    if (
        len(config["ca_pem"]) > 32768
        or hashlib.sha256(config["ca_pem"].encode("ascii")).hexdigest()
        != config["ca_sha256"]
    ):
        raise ValueError("loaded qualification CA changed")


def qualify(config: dict[str, Any]) -> dict[str, Any]:
    validate_context(config)
    start = time.monotonic()
    http = ObservedHTTP(
        DatasetHTTP(config["origin"], config["authorization"], config["ca_pem"]),
        start + 90,
    )
    fixture: Any = importlib.import_module("test_dataset_export").DatasetExportTests()
    stage = "fixture_setup"
    try:
        fixture.setUp()
        stage = "fixture_admission"
        if fixture.item["split"] != config["dataset_split"]:
            raise ValueError("fixture split changed")
        destination = Destination(
            config["origin"],
            config["project_id"],
            config["dataset_id"],
            config["dataset_name"],
            config["dataset_split"],
        )
        projection = DatasetProjection(http, destination)

        def make(source: Any) -> Export:
            return Export(
                source.store,
                fixture.h.registry,
                source,
                fixture.item["item_id"],
                destination,
                approve=fixture.approve,
                permission_ref="export-grant",
            )

        export = make(fixture.source)
        expected = export.body()
        stage = "initial_export"
        try:
            export.dispatch(projection)
        except TimeoutError as exc:
            if str(exc) != "controlled loss after actual dataset acceptance":
                raise
        else:
            raise ValueError("lost-ack challenge not observed")
        if (
            http.posts != 1
            or http.accepted != 1
            or http.faults != 1
            or export.record("attempt") is None
            or export.record("readback") is not None
        ):
            raise ValueError("accepted unresolved attempt required")
        attempts = 0
        stage = "reopen_readback"
        with Store(fixture.source.store.path) as store:
            reopened = make(replace(fixture.source, store=store))
            for attempts in range(1, 16):
                state = reopened.dispatch(projection)
                if state == "delivered":
                    break
                if state != "uncertain" or time.monotonic() >= http.deadline - 1:
                    raise ValueError("readback not settled")
                time.sleep(0.2)
            else:
                raise ValueError("readback attempt bound")
            if reopened.record("readback") is None or http.posts != 1:
                raise ValueError("same outbox exact readback required")
            before = (http.posts, http.gets)
            stage = "current_grant_denial"
            fixture.allow = False
            try:
                reopened.dispatch(projection)
            except ValueError:
                pass
            else:
                raise ValueError("current grant denial missing")
            fixture.allow = True
            stage = "source_withdrawal"
            fixture.h.h.h.invalidate_target("message_deleted")
            try:
                reopened.dispatch(projection)
            except (ValueError, Halt):
                pass
            else:
                raise ValueError("source withdrawal denial missing")
            stage = "registry_withdrawal"
            fixture.h.registry.withdraw(
                fixture.item["item_id"],
                approve=fixture.h.approve,
                permission_ref="explicit-test-grant",
            )
            try:
                fixture.h.registry.available(fixture.item["item_id"])
            except ValueError as exc:
                if str(exc) != "dataset item withdrawn":
                    raise
            else:
                raise ValueError("registry withdrawal missing")
            stage = "local_revocation"
            reopened.revoke(approve=fixture.approve, permission_ref="export-grant")
            try:
                reopened.dispatch(projection)
            except ValueError as exc:
                if str(exc) != "dataset export locally revoked":
                    raise
            else:
                raise ValueError("sticky revocation denial missing")
            if before != (http.posts, http.gets):
                raise ValueError("withdrawal attempted HTTP")
        if time.monotonic() >= http.deadline:
            raise TimeoutError("dataset workflow deadline exceeded")
        stage = "receipt_binding"
        files = sorted(
            set(
                [
                    Path(__file__),
                    ROOT / "tools/langfuse_ephemeral_check.py",
                    ROOT / "dependencies/tether-feedback.json",
                    ROOT / "contracts/dataset-materialization-v1.fixture.json",
                    *ROOT.glob("refinery/*.py"),
                    *ROOT.glob("tests/*.py"),
                ]
            )
        )
        receipt = dict(
            version="refinery.langfuse-dataset-check.v1",
            scope="synthetic_content_external_server_candidate",
            backend_descriptor=config["backend"],
            server_identity="runner_pinned_image_namespace_lifecycle_cleanup_evidence_required_not_inferred_from_HTTP",
            ca_sha256=config["ca_sha256"],
            destination_sha256=c.digest(c.canonical(destination.value())),
            item_sha256=fixture.item["item_id"],
            projection_sha256=export.key,
            body_sha256=c.digest(c.canonical(expected)),
            source_files={
                str(p.relative_to(ROOT)): c.digest(p.read_bytes()) for p in files
            },
            runtime=dict(
                python=platform.python_version(),
                implementation=sys.implementation.name,
                machine=platform.machine(),
                executable_sha256=c.digest(
                    Path(sys.executable).resolve(strict=True).read_bytes()
                ),
                cache_tag=sys.implementation.cache_tag,
            ),
            tether_commit=fixture.source.use.dependency.pin["commit"],
            tether_source_files={
                name: c.digest(raw)
                for name, raw in fixture.source.use.dependency.sources.items()
            },
            post_entries=http.posts,
            accepted_posts=http.accepted,
            injected_lost_ack=http.faults,
            get_entries=http.gets,
            readback_attempts=attempts,
            http_observations=http.calls,
            restart="same_store_reopened_not_server_restart",
            current_grant_denied=True,
            signed_source_withdrawal_denied=True,
            registry_withdrawal_confirmed=True,
            local_sticky_revocation_denied=True,
            remote_cleanup="not_attempted_no_erasure_claim",
            workflow_limit_seconds=90,
            required_external_process_group_supervisor_seconds=110,
        )
        if time.monotonic() >= http.deadline:
            raise TimeoutError("dataset workflow deadline exceeded")
        return receipt
    except Exception as exc:
        raise QualificationFailure(stage, exc, http) from None
    finally:
        fixture.doCleanups()


def main() -> int:
    try:
        if (
            len(sys.argv) != 3
            or os.getpgrp() != os.getpid()
            or os.getsid(0) != os.getpid()
        ):
            raise ValueError("dedicated externally supervised process session required")
        output = Path(sys.argv[2])
        if (
            not output.is_absolute()
            or output.parent.resolve(strict=True) != output.parent
        ):
            raise ValueError("canonical report destination required")
        if output.exists() or output.is_symlink():
            raise ValueError("exclusive report destination required")
        result = qualify(configuration(Path(sys.argv[1])))
        fd = os.open(
            output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(c.canonical(result))
        return 0
    except Exception as exc:
        diagnostic = (
            exc.receipt
            if isinstance(exc, QualificationFailure)
            else dict(
                qualification="failed",
                stage="configuration_or_output",
                error_class="configuration_or_output_error",
                source_sha256=c.digest(Path(__file__).read_bytes()),
            )
        )
        print(
            "DATASET_QUALIFICATION_FAILURE " + c.canonical(diagnostic).decode("ascii"),
            file=sys.stderr,
        )
        print(
            "ephemeral dataset qualification failed; no success receipt",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
