"""Explicit HTTPS transport with a per-request killable process deadline.

No environment credentials/proxies, redirects, retries or global TLS changes.
The worker's stdin carries ephemeral credentials; stdout contains bounded response
bytes only. Errors never reproduce request headers, credentials or server text.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import ssl
import subprocess
import sys
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
        validate_request(method, path, body)
        if type(timeout) is not int or not 1 <= timeout <= 10:
            raise ValueError("bounded request deadline required")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
            raise ValueError("bounded response required")
        payload = json.dumps(
            dict(
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
        if completed.returncode != 0 or len(completed.stdout) > 90000:
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


def worker() -> int:
    """Trusted child; outer process owns the wall-clock deadline and reaping."""
    raw = sys.stdin.buffer.read(160001)
    if len(raw) > 160000:
        return 1
    data = json.loads(raw)
    transport = LangfuseHTTP(data["origin"], data["authorization"], data["ca_pem"])
    body = (
        None if data["body"] is None else base64.b64decode(data["body"], validate=True)
    )
    validate_request(data["method"], data["path"], body)
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
