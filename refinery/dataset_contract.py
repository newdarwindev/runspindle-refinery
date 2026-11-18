"""Shared exact dataset descriptor. Computing identity grants no data permission."""

from __future__ import annotations

import math
import re
from typing import Any

from .feedback_contract import canonical as canonical
from .feedback_contract import digest as digest

VERSION = "runspindle.dataset-materialization.v1"


def finite_json(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [0, 0]
    budget[0] += 1
    if depth > 32 or budget[0] > 10000:
        raise ValueError("JSON structural bound exceeded")
    if type(value) is str:
        budget[1] += len(value.encode("utf-8"))
    elif type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            finite_json(key, depth + 1, budget)
            finite_json(child, depth + 1, budget)
    elif type(value) is list:
        for child in value:
            finite_json(child, depth + 1, budget)
    elif type(value) is float:
        if not math.isfinite(value):
            raise ValueError("finite JSON numbers required")
    elif value is not None and type(value) not in (bool, int):
        raise ValueError("actual JSON types required")
    if budget[1] > 65536:
        raise ValueError("JSON text bound exceeded")


def binding(
    dataset: str,
    source_identity: str,
    input_value: Any,
    output_bytes: bytes,
    family: str,
    split: str,
) -> dict[str, Any]:
    finite_json(input_value)
    if not isinstance(dataset, str) or not re.fullmatch("[a-zA-Z0-9_-]{1,64}", dataset):
        raise ValueError("bounded opaque dataset identifier required")
    for value in (source_identity, family):
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
            raise ValueError("source/family digest required")
    if split not in ("train", "heldout") or type(output_bytes) is not bytes:
        raise ValueError("explicit split and exact artifact bytes required")
    if len(output_bytes) > 65536 or len(canonical(input_value)) > 65536:
        raise ValueError("example byte bound exceeded")
    output_bytes.decode("utf-8")
    descriptor = dict(
        version=VERSION,
        dataset=dataset,
        source_identity_sha256=source_identity,
        input_sha256=digest(canonical(input_value)),
        output_sha256=digest(output_bytes),
        family_sha256=family,
        split=split,
    )
    return dict(descriptor, item_id=digest(canonical(descriptor)))
