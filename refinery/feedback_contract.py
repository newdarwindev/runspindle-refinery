"""Shared opaque projection encoder. Inputs are not authentication or authority."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

VERSION = "runspindle.feedback-projection.v1"
TARGET = {
    "task_id",
    "task_revision",
    "artifact_id",
    "artifact_revision",
    "artifact_sha256",
    "delivery_operation_id",
    "message_id",
    "payload_sha256",
    "workspace_id",
    "channel_id",
    "thread_id",
}
POLICY = {
    "revision",
    "collection_allowed",
    "use_allowed",
    "export_allowed",
    "current_target",
}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text(value: Any) -> None:
    if type(value) is not str or not value or len(value) > 512:
        raise ValueError("bounded nonempty identifier required")


def sha(value: Any) -> None:
    if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("digest required")


def identity(
    namespace: str, feedback_id: str, actor: str, target: dict[str, Any]
) -> str:
    for value in (namespace, feedback_id, actor):
        text(value)
    if type(target) is not dict or set(target) != TARGET:
        raise ValueError("exact target schema required")
    for key, value in target.items():
        if key in ("task_revision", "artifact_revision"):
            if type(value) is not int or value < 1:
                raise ValueError("positive revision required")
        elif key.endswith("sha256"):
            sha(value)
        else:
            text(value)
    return digest(
        canonical(
            dict(
                namespace=namespace,
                feedback_id=feedback_id,
                actor_id=actor,
                target=target,
            )
        )
    )


def policy(value: dict[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != POLICY:
        raise ValueError("exact policy schema required")
    text(value["revision"])
    if any(type(value[k]) is not bool for k in POLICY - {"revision"}):
        raise ValueError("explicit boolean authority required")
    return dict(value)


def projection(
    key: str, revision: int, provenance: str, authority: dict[str, Any], state: str
) -> dict[str, Any]:
    sha(key)
    sha(provenance)
    if (
        type(revision) is not int
        or revision < 1
        or state not in ("active", "revoked", "quarantined")
    ):
        raise ValueError("invalid projection revision/state")
    authority = policy(authority)
    gold = (
        state == "active"
        and authority["current_target"]
        and authority["collection_allowed"]
    )
    body = dict(
        version=VERSION,
        identity_sha256=key,
        revision=revision,
        provenance_sha256=provenance,
        policy_sha256=digest(canonical(authority)),
        state=state,
        gold=gold,
        training_eligible=gold and authority["use_allowed"],
        export_allowed=authority["export_allowed"],
        deployment_authority=False,
    )
    body["projection_id"] = digest(canonical(body))
    return body


def validate(value: dict[str, Any]) -> None:
    if type(value) is not dict or set(value) != {
        "version",
        "identity_sha256",
        "revision",
        "provenance_sha256",
        "policy_sha256",
        "state",
        "gold",
        "training_eligible",
        "export_allowed",
        "deployment_authority",
        "projection_id",
    }:
        raise ValueError("invalid projection fields")
    for key in (
        "identity_sha256",
        "provenance_sha256",
        "policy_sha256",
        "projection_id",
    ):
        sha(value[key])
    if (
        value["version"] != VERSION
        or type(value["revision"]) is not int
        or value["revision"] < 1
        or value["state"] not in ("active", "revoked", "quarantined")
    ):
        raise ValueError("invalid projection identity")
    if (
        any(
            type(value[k]) is not bool
            for k in (
                "gold",
                "training_eligible",
                "export_allowed",
                "deployment_authority",
            )
        )
        or value["deployment_authority"]
        or (value["state"] != "active" and value["gold"])
        or (value["training_eligible"] and not value["gold"])
    ):
        raise ValueError("invalid projection flags")
    if (
        digest(canonical({k: v for k, v in value.items() if k != "projection_id"}))
        != value["projection_id"]
    ):
        raise ValueError("projection digest changed")
