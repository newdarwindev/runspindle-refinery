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
    check_use: Callable[[], None] = lambda: None,
) -> Selection:
    """Choose the highest training exact-match count; deterministic SHA tie break.

    Candidate bytes are fixed before search. This is not an optimizer that edits
    prompts, and synthetic exact-match counts are not model-quality evidence.
    The caller can pass the SAME budget/provider to existing held-out evaluation;
    this function never refunds or resets that budget and never promotes output.
    """
    train, by_digest, script_sha, config_sha = _prepare(
        dataset, split, candidates, provider, budget, per_call, max_attempts
    )
    ledger: Any = budget
    scripted: Any = provider
    initial_clock = scripted.clock
    initial_cursor = len(scripted.calls)
    attempts: list[ev.Attempt] = []
    scores: dict[str, int] = {}
    status, reason = "blocked", None
    selected: bytes | None = None
    selected_sha: str | None = None
    usage: dict[str, Any] | None = None

    def check_provider() -> None:
        reason = None
        try:
            _, _, current_script, current_config = _prepare(
                dataset, split, candidates, provider, budget, per_call, max_attempts
            )
            if (
                scripted.clock is not initial_clock
                or current_script != script_sha
                or current_config != config_sha
            ):
                raise ValueError("changed selection configuration")
        except Exception:
            reason = "adapter_configuration_changed"
        if reason is None and len(scripted.calls) != initial_cursor + len(attempts):
            reason = "provider_cursor_changed"
        if reason is not None:
            ledger.stop(reason)
            raise ev.Halt(reason)

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
                    ev.guard_use(ledger, check_use)
                    check_provider()
                    ticket = ledger.reserve(per_call, request=request)
                    ev.guard_use(ledger, check_use)  # dispatch guard
                    check_provider()
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
                    check_provider()
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
        check_provider()
        usage = ledger.checked_snapshot()
        check_provider()
        selected_sha = min(scores, key=lambda sha: (-scores[sha], sha))
        selected = by_digest[selected_sha]
        status = "selected"
    except ev.Halt as error:
        reason = str(error)
        usage = None
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


def _prepare(
    dataset: Dataset,
    split: LockedSplit,
    candidates: tuple[bytes, ...],
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
    max_attempts: int,
) -> tuple[tuple[Example, ...], dict[str, bytes], str, str]:
    """Validate immutable inputs without ledger writes or provider dispatch."""
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
    reservation: Any = per_call
    if (
        type(scripted.script) is not tuple
        or len(scripted.script) > 4096
        or any(type(reply) is not ev.Completion for reply in scripted.script)
        or type(scripted.calls) is not list
        or type(scripted.clock) is not ev.VirtualClock
        or type(ledger.limits) is not ev.Limits
    ):
        raise ValueError("invalid scripted selection adapter")
    cast(Callable[[], None], ledger.limits.__post_init__)()
    cast(Callable[[], None], reservation.__post_init__)()
    ev._integer(scripted.clock())
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
    return train, by_digest, script_sha, config_sha
