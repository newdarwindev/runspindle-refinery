"""Training-only scripted candidate selection, never DSPy or serving authority.

This process-local adapter extends the existing offline evaluator. It shares its
budget ledger and ScriptedProvider, exposes only identity hashes to that provider,
and never evaluates held-out examples or dispatches a live fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

from . import evaluation as ev
from .lineage import MAX_BYTES, Dataset, Example, LockedSplit, digest

# Existing evaluator adapters are dynamic; retain their runtime checks rather
# than weakening or changing the shared ledger's semantics.
validate_dataset = cast(
    Callable[[Dataset, LockedSplit], tuple[tuple[Example, ...], tuple[Example, ...]]],
    ev._validate_dataset,
)
json_digest = cast(Callable[[Any], str], ev._digest_json)


@dataclass(frozen=True)
class Selection:
    status: str
    reason: str | None
    dataset_sha256: str
    split_sha256: str
    configuration_sha256: str
    provider_script_sha256: str
    selected: bytes | None
    selected_sha256: str | None
    training_cases: tuple[tuple[str, str], ...]
    candidate_scores: tuple[tuple[str, int], ...]
    attempts: tuple[ev.Attempt, ...]
    usage: tuple[tuple[str, Any], ...]
    evidence_class: str = "scripted_training_selection"


def optimize(
    dataset: Dataset,
    split: LockedSplit,
    candidates: tuple[bytes, ...],
    *,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
    max_attempts: int = 1,
) -> Selection:
    """Choose the highest training exact-match count; deterministic SHA tie break.

    Candidate bytes are fixed before search. This is not an optimizer that edits
    prompts, and synthetic exact-match counts are not model-quality evidence.
    The caller can pass the SAME budget/provider to existing held-out evaluation;
    this function never refunds or resets that budget and never promotes output.
    """
    if type(provider) is not ev.ScriptedProvider or not isinstance(budget, ev.Budget):
        raise ValueError("only explicit offline adapters supported")
    if type(per_call) is not ev.Reservation:
        raise ValueError("invalid reservation")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise ValueError("invalid retry bound")
    if type(candidates) is not tuple or not 1 <= len(candidates) <= 16:
        raise ValueError("invalid candidate inventory")
    if any(type(c) is not bytes or not c or len(c) > MAX_BYTES for c in candidates):
        raise ValueError("invalid immutable candidate bytes")
    by_digest = {digest(c): c for c in candidates}
    if len(by_digest) != len(candidates):
        raise ValueError("duplicate candidate identity")
    ledger: Any = budget
    scripted: Any = provider
    if ledger.clock_ms is not scripted.clock:
        raise ValueError("clock binding changed")
    train, _ = validate_dataset(dataset, split)
    if len(train) * len(candidates) * max_attempts > 4096:
        raise ValueError("attempt limit")
    script_sha = str(scripted.sha256)
    config_sha = json_digest(
        {
            "version": "scripted_training_selection.v1",
            "dataset": split.dataset_sha256,
            "split": split.sha256,
            "candidates": sorted(by_digest),
            "script": script_sha,
            "limits": vars(ledger.limits),
            "per_call": vars(per_call),
            "max_attempts": max_attempts,
            "metric": "synthetic_exact_match.v1",
        }
    )
    attempts: list[ev.Attempt] = []
    scores: dict[str, int] = {}
    status, reason = "blocked", None
    selected: bytes | None = None
    selected_sha: str | None = None
    usage: dict[str, Any] | None = None
    try:
        if not train:
            raise ev.Halt("empty_training")
        for sha in sorted(by_digest):
            score = 0
            for example in train:
                for number in range(1, max_attempts + 1):
                    request = ev.Request(
                        "optimization",
                        example.output.output_id,
                        example.output.revision,
                        example.source.sha256,
                        sha,
                        split.sha256,
                        number,
                    )
                    ticket = ledger.reserve(per_call, request=request)
                    try:
                        reply = scripted.complete(request)
                    except Exception:
                        ledger.stop("provider_unknown")
                        attempts.append(
                            ev.Attempt(request, "unknown", None, None, None, None, None)
                        )
                        raise ev.Halt("provider_unknown") from None
                    valid = (
                        type(reply) is ev.Completion
                        and type(reply.outcome) is str
                        and reply.outcome in {"completed", "timeout"}
                        and (type(reply.safety) is bool or reply.safety is None)
                        and type(reply.latency_ms) is int
                        and 0 <= reply.latency_ms <= 2**64 - 1
                        and (
                            (
                                reply.outcome == "completed"
                                and type(reply.content) is bytes
                                and 0 < len(reply.content) <= MAX_BYTES
                            )
                            or (reply.outcome == "timeout" and reply.content is None)
                        )
                    )
                    if not valid:
                        ledger.stop("malformed_reply")
                        attempts.append(
                            ev.Attempt(
                                request, "malformed", None, None, None, None, None
                            )
                        )
                        raise ev.Halt("malformed_reply")
                    attempt = ev.Attempt(
                        request,
                        reply.outcome,
                        digest(reply.content) if reply.content is not None else None,
                        reply.tokens,
                        reply.cost_microunits,
                        reply.safety,
                        reply.latency_ms,
                    )
                    attempts.append(attempt)
                    ledger.settle(
                        ticket,
                        tokens=reply.tokens,
                        cost_microunits=reply.cost_microunits,
                        receipt=attempt,
                    )
                    if reply.safety is not True:
                        ledger.stop(
                            "unknown_safety" if reply.safety is None else "unsafe"
                        )
                        raise ev.Halt(
                            "unknown_safety" if reply.safety is None else "unsafe"
                        )
                    if reply.outcome == "completed":
                        score += int(reply.content == example.gold)
                        break
                    if number == max_attempts:
                        ledger.stop("timeout")
                        raise ev.Halt("timeout")
            scores[sha] = score
        usage = ledger.checked_snapshot()
        selected_sha = min(scores, key=lambda sha: (-scores[sha], sha))
        selected = by_digest[selected_sha]
        status = "selected"
    except ev.Halt as error:
        reason = str(error)
    return Selection(
        status,
        reason,
        split.dataset_sha256,
        split.sha256,
        config_sha,
        script_sha,
        selected,
        selected_sha,
        tuple((e.output.output_id, e.output.revision) for e in train),
        tuple(sorted(scores.items())),
        tuple(attempts),
        tuple(sorted((usage if usage is not None else ledger.snapshot()).items())),
    )
