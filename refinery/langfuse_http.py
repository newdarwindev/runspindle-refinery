"""Explicit HTTPS transport with a per-request killable process deadline.

No environment credentials/proxies, redirects, retries or global TLS changes.
The worker's stdin carries ephemeral credentials; stdout contains bounded response
bytes only. Errors never reproduce request headers, credentials or server text.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import ssl
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def validate_origin(origin: str, ca_pem: str | None) -> None:
    if type(origin) is not str or not 1 <= len(origin) <= 512:
        raise ValueError("explicit bounded HTTPS origin required")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or origin != "https://" + parsed.netloc.lower()
        or parsed.port == 443
        or any(ord(c) <= 32 or ord(c) >= 127 for c in origin)
    ):
        raise ValueError("canonical HTTPS origin required")
    if ca_pem is not None:
        if type(ca_pem) is not str or not 1 <= len(ca_pem) <= 32768:
            raise ValueError("bounded local test CA required")
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("explicit test CA only permitted for loopback HTTPS")
        # Validate without modifying any process-global certificate store.
        ssl.create_default_context(cadata=ca_pem)


def validate_request(method: str, path: str, body: bytes | None) -> None:
    if type(path) is not str or len(path) > 1024:
        raise ValueError("bounded score endpoint required")
    parsed = urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or any(ord(c) <= 32 or ord(c) >= 127 for c in path)
    ):
        raise ValueError("relative score endpoint required")
    if method == "POST":
        if path != "/api/public/scores" or type(body) is not bytes or len(body) > 65536:
            raise ValueError("bounded score POST required")
    elif method == "GET":
        query = parse_qs(parsed.query, strict_parsing=True)
        if (
            parsed.path != "/api/public/v3/scores"
            or set(query) != {"id", "fields", "limit"}
            or query["fields"] != ["details,subject"]
            or query["limit"] != ["2"]
            or len(query["id"]) != 1
            or len(query["id"][0]) != 64
            or any(c not in "0123456789abcdef" for c in query["id"][0])
            or body is not None
        ):
            raise ValueError("exact bounded score readback required")
    else:
        raise ValueError("unsupported score operation")


def validate_dataset_request(method: str, path: str, body: bytes | None) -> None:
    """Text-only immutable descriptor profile, not general Langfuse API access."""
    if type(path) is not str:
        raise ValueError("exact dataset endpoint required")
    if method == "GET":
        if body is not None or not (
            re.fullmatch(r"/api/public/v2/datasets/[A-Za-z0-9_-]{1,64}", path)
            or re.fullmatch(r"/api/public/dataset-items/[0-9a-f]{64}", path)
        ):
            raise ValueError("exact existing dataset read required")
        return
    if (
        method != "POST"
        or path != "/api/public/dataset-items"
        or type(body) is not bytes
        or not 1 <= len(body) <= 262144
    ):
        raise ValueError("bounded dataset item POST required")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def canonical(value: Any) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    count = [0]

    def safe(value: Any, depth: int = 0) -> None:
        count[0] += 1
        if depth > 32 or count[0] > 10000:
            raise ValueError("dataset structural bound")
        if type(value) is str:
            if (
                any(unicodedata.category(ch) in {"Cc", "Cs"} for ch in value)
                or "@@@langfuseMedia:" in value
            ):
                raise ValueError("unsupported dataset text/media")
        elif type(value) is dict:
            for key, child in value.items():
                safe(key, depth + 1)
                safe(child, depth + 1)
        elif type(value) is list:
            for child in value:
                safe(child, depth + 1)

    try:
        value = json.loads(body, object_pairs_hook=unique)
        safe(value)
        if canonical(value) != body:
            raise ValueError("noncanonical dataset JSON")
        if (
            type(value) is not dict
            or set(value)
            != {"id", "datasetName", "input", "expectedOutput", "metadata", "status"}
            or value["status"] != "ACTIVE"
            or type(value["id"]) is not str
            or not re.fullmatch("[0-9a-f]{64}", value["id"])
            or type(value["datasetName"]) is not str
            or not re.fullmatch("[A-Za-z0-9_-]{1,64}", value["datasetName"])
            or type(value["expectedOutput"]) is not str
        ):
            raise ValueError("exact active item required")
        metadata = value["metadata"]
        if (
            type(metadata) is not dict
            or set(metadata) != {"runspindle_dataset", "projection_sha256"}
            or metadata["projection_sha256"] != value["id"]
        ):
            raise ValueError("exact projection metadata required")
        descriptor = metadata["runspindle_dataset"]
        if (
            type(descriptor) is not dict
            or set(descriptor)
            != {
                "version",
                "dataset",
                "source_identity_sha256",
                "input_sha256",
                "output_sha256",
                "family_sha256",
                "split",
                "item_id",
            }
            or descriptor["version"] != "runspindle.dataset-materialization.v1"
            or descriptor["split"] not in ("train", "heldout")
            or type(descriptor["dataset"]) is not str
            or not re.fullmatch("[A-Za-z0-9_-]{1,64}", descriptor["dataset"])
        ):
            raise ValueError("exact dataset descriptor required")
        for key in (
            "source_identity_sha256",
            "input_sha256",
            "output_sha256",
            "family_sha256",
            "item_id",
        ):
            if type(descriptor[key]) is not str or not re.fullmatch(
                "[0-9a-f]{64}", descriptor[key]
            ):
                raise ValueError("descriptor digest required")
        raw_input = canonical(value["input"])
        raw_output = value["expectedOutput"].encode("utf-8")
        without_id = {key: val for key, val in descriptor.items() if key != "item_id"}
        if (
            len(raw_input) > 65536
            or len(raw_output) > 65536
            or hashlib.sha256(raw_input).hexdigest() != descriptor["input_sha256"]
            or hashlib.sha256(raw_output).hexdigest() != descriptor["output_sha256"]
            or hashlib.sha256(canonical(without_id)).hexdigest()
            != descriptor["item_id"]
        ):
            raise ValueError("dataset content identity mismatch")
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ValueError("invalid bounded dataset item") from None


def profile_limits(profile: str) -> tuple[int, int, int]:
    if profile == "score-v1":
        return 65536, 160000, 90000
    if profile == "dataset-v1":
        return 262144, 420000, 350000
    raise ValueError("unknown transport profile")


def validate_profile(profile: str, method: str, path: str, body: bytes | None) -> None:
    profile_limits(profile)
    if profile == "score-v1":
        validate_request(method, path, body)
    else:
        validate_dataset_request(method, path, body)


@dataclass(frozen=True)
class LangfuseHTTP:
    origin: str
    authorization: str = field(repr=False)
    ca_pem: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        validate_origin(self.origin, self.ca_pem)
        if (
            type(self.authorization) is not str
            or not self.authorization.startswith("Basic ")
            or not 7 <= len(self.authorization) <= 4096
            or any(ord(c) <= 32 or ord(c) >= 127 for c in self.authorization[6:])
        ):
            raise ValueError("explicit bounded Basic authorization required")

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout: int,
        max_bytes: int,
    ) -> Any:
        from .langfuse_projection import Response

        self.__post_init__()
        profile = (
            "score-v1"
            if type(self) is LangfuseHTTP
            else "dataset-v1"
            if type(self) is DatasetHTTP
            else "unknown"
        )
        response_limit, _, stdout_limit = profile_limits(profile)
        validate_profile(profile, method, path, body)
        if type(timeout) is not int or not 1 <= timeout <= 10:
            raise ValueError("bounded request deadline required")
        if type(max_bytes) is not int or not 1 <= max_bytes <= response_limit:
            raise ValueError("bounded response required")
        payload = json.dumps(
            dict(
                profile=profile,
                origin=self.origin,
                authorization=self.authorization,
                ca_pem=self.ca_pem,
                method=method,
                path=path,
                body=None if body is None else base64.b64encode(body).decode("ascii"),
                timeout=timeout,
                max_bytes=max_bytes,
            ),
            ensure_ascii=True,
        ).encode()
        # No shell, inherited credentials, proxy settings or Python startup hooks.
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise TimeoutError("bounded Langfuse request unconfirmed") from None
        if completed.returncode != 0 or len(completed.stdout) > stdout_limit:
            raise ValueError("bounded Langfuse request unconfirmed")
        try:
            result = json.loads(completed.stdout)
            if type(result) is not dict or set(result) != {"status", "body"}:
                raise ValueError
            raw = base64.b64decode(result["body"], validate=True)
            if type(result["status"]) is not int or len(raw) > max_bytes:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError("invalid bounded transport response") from None
        return Response(result["status"], raw)


@dataclass(frozen=True)
class DatasetHTTP(LangfuseHTTP):
    """Explicit dataset-only profile; grants and destination stay in Export."""


def worker() -> int:
    """Trusted child; outer process owns the wall-clock deadline and reaping."""
    raw = sys.stdin.buffer.read(420001)
    if len(raw) > 420000:
        return 1

    def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate worker field")
            value[key] = item
        return value

    data = json.loads(raw, object_pairs_hook=unique_fields)
    if type(data) is not dict or set(data) != {
        "profile",
        "origin",
        "authorization",
        "ca_pem",
        "method",
        "path",
        "body",
        "timeout",
        "max_bytes",
    }:
        return 1
    response_limit, stdin_limit, _ = profile_limits(data["profile"])
    if (
        len(raw) > stdin_limit
        or type(data["timeout"]) is not int
        or not 1 <= data["timeout"] <= 10
        or type(data["max_bytes"]) is not int
        or not 1 <= data["max_bytes"] <= response_limit
    ):
        return 1
    transport = LangfuseHTTP(data["origin"], data["authorization"], data["ca_pem"])
    body = (
        None if data["body"] is None else base64.b64decode(data["body"], validate=True)
    )
    validate_profile(data["profile"], data["method"], data["path"], body)
    context = ssl.create_default_context(cadata=transport.ca_pem)
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
    request = Request(
        transport.origin + data["path"],
        data=body,
        method=data["method"],
        headers={
            "Authorization": transport.authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    try:
        response = opener.open(request, timeout=data["timeout"])
    except HTTPError as exc:
        response = exc
    with response:
        output = response.read(data["max_bytes"] + 1)
        if len(output) > data["max_bytes"]:
            return 1
        result = dict(
            status=response.status, body=base64.b64encode(output).decode("ascii")
        )
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        code = worker() if sys.argv[1:] == ["--worker"] else 1
    except BaseException:
        code = 1
    raise SystemExit(code)
