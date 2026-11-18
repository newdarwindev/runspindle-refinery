"""Pure, bounded GEPA request evidence; no framework imports or execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import evaluation as ev
from . import gepa_codec as codec
from .dspy_optimizer import case
from .lineage import digest

LIFECYCLE = "refinery.gepa.lifecycle.v1"


@dataclass(frozen=True)
class GEPACompiled:
    status: str
    reason: str | None
    configuration_sha256: str
    candidate: bytes | None
    candidate_sha256: str | None
    attempts: tuple[ev.Attempt, ...]
    usage: tuple[tuple[str, Any], ...]
    evidence_class: str = "offline_gepa_compile_component"


def validate_shape(witness: Any, request: Any) -> None:
    if (
        type(witness) is not tuple
        or len(witness) != 3
        or type(request) is not ev.Request
        or type(witness[0]) is not ev.Request
        or witness[0] != request
        or request.stage not in {"optimization", "reflection"}
    ):
        raise ValueError("invalid GEPA request witness")
    codec.text(witness[1])
    codec.text(witness[2], 65536)


def validate_history(
    rows: Any, witnesses: Any, example: Any, split: Any, config: str, script: Any
) -> None:
    """Bind even unresolved requests to preimages and earlier settled receipts.

    Script bytes are immutable session inputs, not provider callbacks. Unknown
    receipts cannot authorize a proposal or a changed instruction.
    """
    if (
        type(rows) is not tuple
        or type(witnesses) is not tuple
        or len(rows) != len(witnesses)
        or len(rows) > 16
    ):
        raise ValueError("invalid GEPA witness count")
    settled: list[tuple[Any, Any, Any]] = []
    instructions = {codec.INITIAL}
    reflections = 0
    terminal = False
    for position, ((request, outcome, receipt), witness) in enumerate(
        zip(rows, witnesses), 1
    ):
        if terminal:
            raise ValueError("GEPA request follows terminal receipt")
        validate_shape(witness, request)
        _, instruction, payload = witness
        if (
            request.attempt != position
            or type(request.attempt) is not int
            or (
                request.output_id,
                request.output_revision,
                request.source_sha256,
                request.split_sha256,
            )
            != (
                example.output.output_id,
                example.output.revision,
                example.source.sha256,
                split.sha256,
            )
            or request.artifact_sha256
            != codec.association(config, request.stage, instruction, payload)
        ):
            raise ValueError("GEPA request association changed")
        if request.stage == "optimization":
            if instruction not in instructions or payload != case(example):
                raise ValueError("unattributed GEPA instruction")
        else:
            reflections += 1
            if instruction != codec.INITIAL or reflections > 2:
                raise ValueError("invalid GEPA reflection parent")
            matches = [
                a
                for prior, answer, a in settled
                if prior == codec.INITIAL
                and a.request.stage == "optimization"
                and answer != example.gold.decode()
                and codec.proposal_payload(
                    config, case(example), answer, "incorrect", a
                )
                == payload
            ]
            if len(matches) != 1:
                raise ValueError("unattributable GEPA reflection payload")
        if outcome not in {"settled", "unknown"}:
            raise ValueError("invalid GEPA reservation outcome")
        if receipt is None:
            if outcome != "unknown":
                raise ValueError("missing GEPA settled receipt")
            terminal = True
            continue
        if type(receipt) is not ev.Attempt or receipt.request != request:
            raise ValueError("GEPA receipt association changed")
        if outcome == "unknown":
            terminal = True
            continue
        if position > len(script):
            raise ValueError("GEPA script receipt missing")
        reply = script[position - 1]
        if (
            type(reply) is not ev.Completion
            or receipt.content_sha256
            != (digest(reply.content) if type(reply.content) is bytes else None)
            or (
                reply.outcome,
                reply.tokens,
                reply.cost_microunits,
                reply.safety,
                reply.latency_ms,
            )
            != (
                receipt.outcome,
                receipt.tokens,
                receipt.cost_microunits,
                receipt.safety,
                receipt.latency_ms,
            )
        ):
            raise ValueError("GEPA settled script changed")
        if receipt.outcome != "completed" or receipt.safety is not True:
            terminal = True
            continue
        if type(reply.content) is not bytes:
            raise ValueError("GEPA successful content missing")
        answer = codec.text(reply.content.decode())
        settled.append((instruction, answer, receipt))
        if request.stage == "reflection":
            if answer == codec.INITIAL:
                raise ValueError("unchanged GEPA proposal")
            instructions.add(answer)
