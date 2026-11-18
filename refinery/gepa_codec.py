"""Separate instruction codec, admitted only by the sealed GEPA strategy."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast

from . import evaluation as ev
from .dspy_optimizer import canonical, case
from .lineage import Dataset, LockedSplit, digest
from .optimization import validate_dataset

VERSION = "refinery.gepa.instruction.v1"
INITIAL = "Answer the case using its source. Return only the answer."
COMPONENT = "predict"


def text(value: Any, maximum: int = 4096) -> str:
    if type(value) is not str or not value or len(value.encode()) > maximum:
        raise ValueError("invalid bounded text")
    return value


def association(config: str, stage: str, instruction: str, payload: str) -> str:
    if (
        type(config) is not str
        or len(config) != 64
        or any(c not in "0123456789abcdef" for c in config)
    ):
        raise ValueError("invalid configuration digest")
    if stage not in {"optimization", "reflection"}:
        raise ValueError("invalid reflection stage")
    return digest(
        canonical(
            {
                "format": VERSION,
                "configuration": config,
                "stage": stage,
                "instruction": text(instruction),
                "payload": text(payload, 65536),
            }
        )
    )


def proposal_payload(
    config: str, value: str, answer: str, feedback: str, attempt: ev.Attempt
) -> str:
    # Caller/decoder must additionally establish the exact training association.
    if (
        type(attempt) is not ev.Attempt
        or attempt.outcome != "completed"
        or attempt.safety is not True
        or attempt.content_sha256 != digest(text(answer).encode())
        or attempt.request.stage != "optimization"
        or attempt.request.artifact_sha256
        != association(config, "optimization", INITIAL, value)
    ):
        raise ValueError("unverified reflection trace")
    return text(
        canonical(
            {
                "format": VERSION,
                "configuration": config,
                "component": COMPONENT,
                "parent_instruction": INITIAL,
                "case": value,
                "answer": answer,
                "feedback": feedback,
                "attempt": asdict(attempt),
            }
        ).decode(),
        65536,
    )


def encode(
    instruction: str,
    payload: str,
    reflection: ev.Attempt,
    dataset: Dataset,
    split: LockedSplit,
    config: str,
    attempts: tuple[ev.Attempt, ...],
) -> bytes:
    instruction = text(instruction)
    if instruction == INITIAL or type(attempts) is not tuple:
        raise ValueError("no attributable instruction change")
    train, _ = validate_dataset(dataset, split)
    data = json.loads(text(payload, 65536))
    if (
        type(data) is not dict
        or not 3 <= len(attempts) <= 16
        or any(type(a) is not ev.Attempt for a in attempts)
    ):
        raise ValueError("invalid proposal receipts")
    selected = [e for e in train if case(e) == data.get("case")]
    if len(selected) != 1:
        raise ValueError("reflection requires training example")
    e = selected[0]
    feedback = "correct" if data.get("answer") == e.gold.decode() else "incorrect"
    trace = [a for a in attempts if asdict(a) == data.get("attempt")]
    if (
        len(trace) != 1
        or proposal_payload(
            config, case(e), text(data.get("answer")), feedback, trace[0]
        )
        != payload
    ):
        raise ValueError("reflection trace association changed")
    if feedback != "incorrect":
        raise ValueError("reflection must address an observed failure")
    for index, a in enumerate(attempts, 1):
        if (
            a.outcome != "completed"
            or a.safety is not True
            or any(
                type(v) is not int or v < 0
                for v in (a.tokens, a.cost_microunits, a.latency_ms)
            )
            or a.request.attempt != index
        ):
            raise ValueError("unsettled or reordered proposal receipts")
        r = a.request
        if (r.output_id, r.output_revision, r.source_sha256, r.split_sha256) != (
            e.output.output_id,
            e.output.revision,
            e.source.sha256,
            split.sha256,
        ):
            raise ValueError("reflection request association changed")
    if (
        reflection not in attempts
        or reflection.outcome != "completed"
        or reflection.safety is not True
        or reflection.request.stage != "reflection"
        or reflection.request.artifact_sha256
        != association(config, "reflection", INITIAL, payload)
        or reflection.content_sha256 != digest(instruction.encode())
    ):
        raise ValueError("unverified instruction proposal")
    reflection_index = attempts.index(reflection)
    for index, a in enumerate(attempts):
        expected = association(
            config,
            "optimization",
            INITIAL if index < reflection_index else instruction,
            case(e),
        )
        if index == reflection_index:
            continue
        if a.request.stage != "optimization" or a.request.artifact_sha256 != expected:
            raise ValueError("foreign dispatch in instruction history")
    if not any(
        a.content_sha256 == digest(e.gold) for a in attempts[reflection_index + 1 :]
    ):
        raise ValueError("reflected instruction not successfully evaluated")
    return canonical(
        {
            "format": VERSION,
            "dspy": "3.1.0",
            "gepa": "0.0.22",
            "signature": "case -> answer",
            "component": COMPONENT,
            "configuration_sha256": config,
            "dataset_sha256": split.dataset_sha256,
            "split_sha256": split.sha256,
            "instruction": instruction,
            "proposal": data,
            "reflection_attempt": asdict(reflection),
            "attempts_sha256": digest(canonical([asdict(a) for a in attempts])),
        }
    )


def decode(
    candidate: bytes,
    expected_sha256: str,
    dataset: Dataset,
    split: LockedSplit,
    config: str,
    attempts: tuple[ev.Attempt, ...],
) -> str:
    if (
        type(candidate) is not bytes
        or len(candidate) > 131072
        or digest(candidate) != expected_sha256
    ):
        raise ValueError("locked instruction digest changed")
    try:
        data = json.loads(candidate)
        matches = [a for a in attempts if asdict(a) == data["reflection_attempt"]]
        if (
            len(matches) != 1
            or encode(
                data["instruction"],
                canonical(data["proposal"]).decode(),
                matches[0],
                dataset,
                split,
                config,
                attempts,
            )
            != candidate
        ):
            raise ValueError("instruction association changed")
        return cast(str, data["instruction"])
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid instruction candidate") from exc


def baseline_artifact() -> str:
    """Distinct immutable empty-demo instruction baseline, not Bootstrap demos."""
    return digest(
        canonical(
            {
                "format": "refinery.gepa.baseline.v1",
                "signature": "case -> answer",
                "component": COMPONENT,
                "instruction": INITIAL,
                "demonstrations": [],
            }
        )
    )
