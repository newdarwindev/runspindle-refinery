"""Metadata-only Langfuse scores API adapter; caller owns approved HTTP transport.

No credentials, URLs, SDK imports, network defaults or remote-authority fallback.
POST acceptance is never delivery. Only exact v3 score readback can match.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from . import feedback_contract as contract

NAME = "runspindle.feedback.v1"
MAX_BYTES = 65536


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class HTTP(Protocol):
    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout: int,
        max_bytes: int,
    ) -> Response: ...


@dataclass(frozen=True)
class LangfuseProjection:
    http: HTTP
    project_id: str
    trace_id: str

    def __post_init__(self) -> None:
        contract.text(self.project_id)
        contract.text(self.trace_id)

    @property
    def destination(self) -> str:
        return "langfuse-v3:" + contract.digest(
            contract.canonical(
                {"project_id": self.project_id, "trace_id": self.trace_id}
            )
        )

    def _request(self, method: str, path: str, body: bytes | None) -> tuple[int, Any]:
        result = self.http.request(method, path, body, timeout=10, max_bytes=MAX_BYTES)
        if (
            type(result) is not Response
            or type(result.status) is not int
            or type(result.body) is not bytes
            or len(result.body) > MAX_BYTES
        ):
            raise ValueError("invalid bounded Langfuse response")
        # HTTP errors, 404 and malformed JSON are NOT proof of score absence.
        if result.status != 200:
            raise ValueError("Langfuse request not confirmed")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            out = {}
            for key, value in items:
                if key in out:
                    raise ValueError("duplicate Langfuse field")
                out[key] = value
            return out

        return result.status, json.loads(result.body, object_pairs_hook=pairs)

    def put(self, value: dict[str, Any]) -> None:
        contract.validate(value)
        if not value["export_allowed"]:
            raise ValueError("projection export denied")
        body = dict(
            id=value["projection_id"],
            traceId=self.trace_id,
            name=NAME,
            value=int(value["gold"]),
            dataType="BOOLEAN",
            source="API",
            metadata=value,
        )
        _, response = self._request(
            "POST", "/api/public/scores", contract.canonical(body)
        )
        if type(response) is not dict or response.get("id") != value["projection_id"]:
            raise ValueError("Langfuse acceptance identity mismatch")

    def get(self, projection_id: str) -> dict[str, Any] | None:
        contract.sha(projection_id)
        query = urlencode(dict(id=projection_id, fields="details,subject", limit=2))
        _, response = self._request("GET", "/api/public/v3/scores?" + query, None)
        if (
            type(response) is not dict
            or set(response) != {"data", "meta"}
            or type(response["data"]) is not list
            or type(response["meta"]) is not dict
            or response["meta"].get("cursor")
        ):
            raise ValueError("incomplete Langfuse readback")
        rows = response["data"]
        if not rows:
            return None  # Not yet visible; never an exactly-once absence claim.
        if len(rows) != 1 or type(rows[0]) is not dict:
            raise ValueError("ambiguous Langfuse score")
        score = rows[0]
        value: Any = score.get("metadata")
        contract.validate(value)
        if (
            score.get("id") != projection_id
            or value["projection_id"] != projection_id
            or score.get("projectId") != self.project_id
            or score.get("name") != NAME
            or score.get("dataType") != "BOOLEAN"
            or score.get("source") != "API"
            or type(score.get("value")) is not bool
            or score["value"] != value["gold"]
            or score.get("subject") != {"kind": "trace", "id": self.trace_id}
        ):
            raise ValueError("Langfuse score association mismatch")
        return dict(value)
