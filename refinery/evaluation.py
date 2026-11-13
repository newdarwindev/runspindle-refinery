"""Bounded offline synthetic evaluation; no network, approval or serving interface.

Only ScriptedProvider is supported. Reports are process-local component evidence,
not provider measurements, durable registry records or general quality evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Callable

from .lineage import Dataset, LockedSplit, MAX_BYTES, MAX_RECORDS, digest


VERSION = "runspindle.runtime.v1"


def _integer(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0) or value > 2**64 - 1:
        raise ValueError("invalid bounded integer")


def _hash(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid digest")


def _bytes(value):
    if type(value) is not bytes or not value or len(value) > MAX_BYTES:
        raise ValueError("invalid immutable bytes")


def _digest_json(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


@dataclass(frozen=True)
class Limits:
    calls: int
    tokens: int
    time_ms: int
    cost_microunits: int

    def __post_init__(self):
        for value in vars(self).values():
            _integer(value)


@dataclass(frozen=True)
class Reservation:
    tokens: int
    cost_microunits: int

    def __post_init__(self):
        _integer(self.tokens)
        _integer(self.cost_microunits)


class Halt(Exception):
    """A bounded run cannot continue; the reason contains no provider text."""


class Budget:
    """One thread-safe ledger. Pending upper bounds reserve concurrent capacity.

    A missing/invalid receipt halts the ledger with its reservation retained;
    no unknown usage is refunded. This is memory-only, not a billing system.
    """
    def __init__(self, limits: Limits, *, clock_ms: Callable[[], int]):
        if type(limits) is not Limits:
            raise ValueError("invalid limits")
        self.limits, self.clock_ms = limits, clock_ms
        self._lock = threading.Lock()
        self._start = self._read_clock()
        self._last = self._start
        self._pending = {}
        self._next = 0
        self.calls = self.tokens = self.cost_microunits = 0
        self.reason = None
        self._time_unknown = False

    def _read_clock(self):
        value = self.clock_ms()
        _integer(value)
        return value

    def _check(self):
        try:
            now = self._read_clock()
        except Exception:
            self._time_unknown = True
            self.reason = self.reason or "invalid_clock"
            raise Halt(self.reason) from None
        if now < self._last:
            self._time_unknown = True
            self.reason = self.reason or "invalid_clock"
        elif now - self._start >= self.limits.time_ms:
            self.reason = self.reason or "time_exhausted"
        self._last = max(self._last, now)
        if self.reason:
            raise Halt(self.reason)

    def cancel(self):
        with self._lock:
            self.reason = self.reason or "cancelled"

    def stop(self, reason):
        with self._lock:
            self.reason = self.reason or reason

    def reserve(self, maximum: Reservation) -> int:
        if type(maximum) is not Reservation:
            raise ValueError("invalid reservation")
        with self._lock:
            self._check()
            held_tokens = sum(r.tokens for r in self._pending.values())
            held_cost = sum(r.cost_microunits for r in self._pending.values())
            if self.calls >= self.limits.calls:
                self.reason = "calls_exhausted"
            elif self.tokens + held_tokens + maximum.tokens > self.limits.tokens:
                self.reason = "tokens_exhausted"
            elif self.cost_microunits + held_cost + maximum.cost_microunits > self.limits.cost_microunits:
                self.reason = "cost_exhausted"
            if self.reason:
                raise Halt(self.reason)
            self.calls += 1
            self._next += 1
            self._pending[self._next] = maximum
            return self._next

    def settle(self, ticket: int, *, tokens: int | None, cost_microunits: int | None):
        _integer(ticket, positive=True)
        with self._lock:
            if ticket not in self._pending:
                raise ValueError("unknown or already settled reservation")
            maximum = self._pending[ticket]
            try:
                _integer(tokens)
                _integer(cost_microunits)
                if tokens > maximum.tokens or cost_microunits > maximum.cost_microunits:
                    raise ValueError("receipt exceeds reservation")
            except ValueError:
                self.reason = self.reason or "unknown_or_invalid_usage"
                raise Halt(self.reason) from None
            del self._pending[ticket]
            self.tokens += tokens
            self.cost_microunits += cost_microunits
            self._check()

    def check(self):
        with self._lock:
            self._check()

    def _snapshot_locked(self):
        return {"calls": self.calls, "tokens": self.tokens if not self._pending else None,
                    "elapsed_ms": None if self._time_unknown else self._last - self._start,
                    "cost_microunits": self.cost_microunits if not self._pending else None,
                    "unresolved_calls": len(self._pending), "reason": self.reason}

    def snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def checked_snapshot(self):
        """Linearize completion with cancellation and concurrent reservations."""
        with self._lock:
            self._check()
            if self._pending:
                self.reason = "unresolved_usage"
                raise Halt(self.reason)
            return self._snapshot_locked()


class VirtualClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, latency_ms):
        _integer(latency_ms)
        self.now += latency_ms


@dataclass(frozen=True)
class Completion:
    content: bytes | None
    tokens: int | None
    cost_microunits: int | None
    safety: bool | None
    latency_ms: int = 0
    outcome: str = "completed"  # timeout is known scripted failure, never real retry authority


@dataclass(frozen=True)
class Request:
    stage: str
    output_id: str
    output_revision: str
    source_sha256: str
    artifact_sha256: str
    split_sha256: str
    attempt: int


class ScriptedProvider:
    """An explicit offline adapter; no fallback and no callback to a live provider."""
    def __init__(self, script: tuple[Completion, ...], *, clock: VirtualClock):
        if type(script) is not tuple or len(script) > 4096 or any(type(x) is not Completion for x in script):
            raise ValueError("invalid bounded script")
        if type(clock) is not VirtualClock:
            raise ValueError("script requires virtual clock")
        self.script, self.clock = script, clock
        self.calls = []

    @property
    def sha256(self):
        return _digest_json([{**vars(r), "content": digest(r.content) if type(r.content) is bytes else r.content}
                             for r in self.script])

    def complete(self, request: Request) -> Completion:
        self.calls.append(request)
        index = len(self.calls) - 1
        if index >= len(self.script):
            raise ValueError("script exhausted")
        result = self.script[index]
        self.clock.advance(result.latency_ms)
        return result


@dataclass(frozen=True)
class Configuration:
    """All executed controls are part of these canonical, hash-locked bytes."""
    limits: Limits
    per_call: Reservation
    provider_script_sha256: str
    min_score_ppm: int
    min_improvement_ppm: int
    max_attempts: int
    preparation: tuple[str, ...]
    metric: str = "synthetic_exact_match.v1"
    provider: str = "scripted.v1"
    optimizer: str = "scripted_stage.v1"
    currency: str = "SYNTHETIC"
    cost_scale: int = 1_000_000

    def __post_init__(self):
        if type(self.limits) is not Limits or type(self.per_call) is not Reservation:
            raise ValueError("invalid budget configuration")
        _hash(self.provider_script_sha256)
        _integer(self.cost_scale, positive=True)
        if (self.metric, self.provider, self.optimizer, self.currency, self.cost_scale) != (
                "synthetic_exact_match.v1", "scripted.v1", "scripted_stage.v1", "SYNTHETIC", 1_000_000):
            raise ValueError("unsupported offline configuration")
        if type(self.preparation) is not tuple or len(self.preparation) > 16 or any(s not in {"optimization", "reflection"} for s in self.preparation):
            raise ValueError("invalid preparation stages")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("invalid retry bound")
        for value in (self.min_score_ppm, self.min_improvement_ppm):
            _integer(value)
            if value > 1_000_000:
                raise ValueError("invalid metric threshold")

    @property
    def content(self):
        return json.dumps({**vars(self), "limits": vars(self.limits), "per_call": vars(self.per_call)},
                          sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    @property
    def sha256(self):
        return digest(self.content)


@dataclass(frozen=True)
class Experiment:
    dataset_sha256: str
    split_sha256: str
    baseline: bytes
    baseline_sha256: str
    candidate: bytes
    candidate_sha256: str
    configuration: Configuration
    config_sha256: str

    def __post_init__(self):
        for value in (self.dataset_sha256, self.split_sha256, self.baseline_sha256,
                      self.candidate_sha256, self.config_sha256):
            _hash(value)
        for content, expected in ((self.baseline, self.baseline_sha256), (self.candidate, self.candidate_sha256)):
            _bytes(content)
            if digest(content) != expected:
                raise ValueError("artifact bytes changed")
        if type(self.configuration) is not Configuration or self.configuration.sha256 != self.config_sha256:
            raise ValueError("configuration bytes changed")


@dataclass(frozen=True)
class Attempt:
    request: Request
    outcome: str
    content_sha256: str | None
    tokens: int | None
    cost_microunits: int | None
    safety: bool | None
    latency_ms: int | None


@dataclass(frozen=True)
class Metrics:
    quality: float | None
    safety: bool | None
    calls: int
    tokens: int | None
    cost_microunits: int | None
    latency_ms: int | None


@dataclass(frozen=True)
class Report:
    version: str
    status: str
    reason: str | None
    dataset_sha256: str
    split_sha256: str
    baseline_sha256: str
    candidate_sha256: str
    config_sha256: str
    heldout_cases: tuple[tuple[str, str], ...]
    excluded_counts: tuple[tuple[str, int], ...]
    attempts: tuple[Attempt, ...]
    baseline_score: float | None
    candidate_score: float | None
    safety: bool | None
    usage: tuple[tuple[str, int | str | None], ...]

    def _metrics(self, stage, quality):
        selected = tuple(a for a in self.attempts if a.request.stage == stage)
        def total(field):
            values = [getattr(a, field) for a in selected]
            return sum(values) if values and all(type(v) is int and v >= 0 for v in values) else None
        safe = [a.safety for a in selected]
        safety = None if not safe or any(s is None for s in safe) else all(safe)
        return Metrics(quality, safety, len(selected), total("tokens"), total("cost_microunits"), total("latency_ms"))

    @property
    def baseline_metrics(self):
        return self._metrics("baseline", self.baseline_score)

    @property
    def candidate_metrics(self):
        return self._metrics("candidate", self.candidate_score)


def _validate_dataset(dataset, split):
    if type(dataset) is not Dataset or type(split) is not LockedSplit or len(dataset.examples) > MAX_RECORDS:
        raise ValueError("invalid dataset or split")
    split.verify(dataset)
    # A split lock is integrity, not authentication. Recheck immutable byte links.
    seen = set()
    for example in dataset.examples:
        s, o = example.source, example.output
        identity = (o.output_id, o.revision)
        if identity in seen or not s.complete or not o.complete or not example.feedback:
            raise ValueError("invalid lineage")
        seen.add(identity)
        if (digest(s.content) != s.sha256 or digest(o.content) != o.sha256
                or (o.source_id, o.source_revision, o.source_sha256) != (s.source_id, s.revision, s.sha256)):
            raise ValueError("changed lineage bytes")
        _bytes(example.gold)
    return tuple(e for e in dataset.examples if split.partition(e.source.family_id) == "train"), tuple(
        e for e in dataset.examples if split.partition(e.source.family_id) == "heldout")


def evaluate(dataset: Dataset, split: LockedSplit, experiment: Experiment, *,
             provider: ScriptedProvider, budget: Budget) -> Report:
    """Evaluate exact pinned artifacts on one lock; passing grants no authority.

    All stages share the supplied ledger. Only explicit scripted timeout receipts
    may retry. Unknown/malformed replies stop with zero downstream calls.
    """
    if type(experiment) is not Experiment or type(provider) is not ScriptedProvider or type(budget) is not Budget:
        raise ValueError("offline adapters required")
    experiment.__post_init__()
    config = experiment.configuration
    config.__post_init__()
    if (budget.limits != config.limits or provider.sha256 != config.provider_script_sha256
            or budget.clock_ms is not provider.clock):
        raise ValueError("adapter configuration changed")
    train, heldout = _validate_dataset(dataset, split)
    if (experiment.dataset_sha256, experiment.split_sha256) != (split.dataset_sha256, split.sha256):
        raise ValueError("manifest binding changed")
    # The request object contains hashes/identity only, not gold or feedback text.
    attempts, scores, safeties = [], {"baseline": [], "candidate": []}, []
    reason, status, usage = None, "blocked", None
    try:
        if not heldout:
            raise Halt("empty_evaluation")
        if config.preparation and not train:
            raise Halt("empty_training")
        sequence = [(stage, e) for stage in config.preparation for e in train]
        sequence += [(stage, e) for stage in ("baseline", "candidate") for e in heldout]
        if len(sequence) * config.max_attempts > 4096:
            raise Halt("attempt_limit")
        for stage, example in sequence:
            artifact = experiment.baseline_sha256 if stage == "baseline" else experiment.candidate_sha256
            for number in range(1, config.max_attempts + 1):
                request = Request(stage, example.output.output_id, example.output.revision,
                                  example.source.sha256, artifact, split.sha256, number)
                ticket = budget.reserve(config.per_call)
                try:
                    reply = provider.complete(request)
                except Exception:
                    budget.stop("provider_unknown")
                    attempts.append(Attempt(request, "unknown", None, None, None, None, None))
                    raise Halt("provider_unknown") from None
                valid = (type(reply) is Completion and reply.outcome in {"completed", "timeout"}
                         and (type(reply.safety) is bool or reply.safety is None))
                if valid:
                    try:
                        _integer(reply.latency_ms)
                        if reply.outcome == "completed":
                            _bytes(reply.content)
                        elif reply.content is not None:
                            valid = False
                    except ValueError:
                        valid = False
                if not valid:
                    budget.stop("malformed_reply")
                    attempts.append(Attempt(request, "malformed", None, None, None, None, None))
                    raise Halt("malformed_reply")
                attempts.append(Attempt(request, reply.outcome,
                                        digest(reply.content) if reply.content is not None else None,
                                        reply.tokens, reply.cost_microunits, reply.safety, reply.latency_ms))
                budget.settle(ticket, tokens=reply.tokens, cost_microunits=reply.cost_microunits)
                safeties.append(reply.safety)
                if reply.safety is not True:
                    raise Halt("unknown_safety" if reply.safety is None else "unsafe")
                if reply.outcome == "completed":
                    if stage in scores:
                        scores[stage].append(int(reply.content == example.gold))
                    break
                if number == config.max_attempts:
                    raise Halt("timeout")
        if sum(scores["candidate"]) * 1_000_000 < config.min_score_ppm * len(heldout) or (sum(scores["candidate"]) - sum(scores["baseline"])) * 1_000_000 < config.min_improvement_ppm * len(heldout):
            raise Halt("threshold_failed")
        usage = budget.checked_snapshot()
        status = "passed"
    except Halt as exc:
        reason = str(exc)
    baseline_score = sum(scores["baseline"]) / len(heldout) if heldout and len(scores["baseline"]) == len(heldout) else None
    candidate_score = sum(scores["candidate"]) / len(heldout) if heldout and len(scores["candidate"]) == len(heldout) else None
    safety = None if not safeties or any(s is None for s in safeties) else all(safeties)
    return Report(VERSION, status, reason, split.dataset_sha256, split.sha256,
                  experiment.baseline_sha256, experiment.candidate_sha256, experiment.config_sha256,
                  tuple((e.output.output_id, e.output.revision) for e in heldout),
                  tuple(sorted(dataset.excluded_counts.items())), tuple(attempts),
                  baseline_score, candidate_score, safety, tuple(sorted((usage if usage is not None else budget.snapshot()).items())))
