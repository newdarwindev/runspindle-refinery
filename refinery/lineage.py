"""Bounded, process-local lineage logic. Not an authentication or provider adapter."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Iterable

MAX_RECORDS = 1000
MAX_BYTES = 1_048_576
MAX_TOTAL_BYTES = 8_388_608


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _token(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError("invalid identifier")


def _hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA256")


def _bytes(value: bytes) -> None:
    if not isinstance(value, bytes) or len(value) > MAX_BYTES:
        raise ValueError("content must be bounded immutable bytes")


@dataclass(frozen=True)
class SourceRevision:
    source_id: str
    revision: str
    family_id: str
    content: bytes
    sha256: str
    complete: bool = True

    def __post_init__(self):
        for value in (self.source_id, self.revision, self.family_id):
            _token(value)
        _bytes(self.content)
        _hash(self.sha256)
        if type(self.complete) is not bool or digest(self.content) != self.sha256:
            raise ValueError("invalid source bytes or completeness")


@dataclass(frozen=True)
class OutputRevision:
    output_id: str
    revision: str
    task_type: str
    source_id: str
    source_revision: str
    source_sha256: str
    content: bytes
    sha256: str
    complete: bool = True

    def __post_init__(self):
        for value in (self.output_id, self.revision, self.task_type, self.source_id, self.source_revision):
            _token(value)
        _bytes(self.content)
        _hash(self.source_sha256)
        _hash(self.sha256)
        if type(self.complete) is not bool or digest(self.content) != self.sha256:
            raise ValueError("invalid output bytes or completeness")


@dataclass(frozen=True)
class Feedback:
    feedback_id: str
    actor_id: str
    output_id: str
    output_revision: str
    output_sha256: str
    rule_id: str
    kind: str  # positive, negative, correction; rules explicitly allow each gold kind
    correction: bytes | None = None
    target_available: bool = True

    def __post_init__(self):
        for value in (self.feedback_id, self.actor_id, self.output_id, self.output_revision, self.rule_id):
            _token(value)
        _hash(self.output_sha256)
        if self.kind not in {"positive", "negative", "correction"}:
            raise ValueError("unsupported feedback kind")
        if type(self.target_available) is not bool:
            raise ValueError("invalid target availability")
        if self.kind == "correction":
            _bytes(self.correction)
        elif self.correction is not None:
            raise ValueError("unexpected correction bytes")


@dataclass(frozen=True)
class GoldRule:
    task_type: str
    rule_id: str
    kinds: tuple[str, ...]

    def __post_init__(self):
        _token(self.task_type)
        _token(self.rule_id)
        if not isinstance(self.kinds, tuple) or not self.kinds or not set(self.kinds) <= {"positive", "correction"}:
            raise ValueError("invalid task gold rule")


@dataclass(frozen=True)
class Example:
    source: SourceRevision
    output: OutputRevision
    gold: bytes
    rule_id: str
    feedback: tuple[Feedback, ...]

    @property
    def feedback_ids(self) -> tuple[str, ...]:
        return tuple(f.feedback_id for f in self.feedback)

    @property
    def actor_ids(self) -> tuple[str, ...]:
        return tuple(sorted({f.actor_id for f in self.feedback}))


@dataclass(frozen=True)
class Quarantine:
    feedback_id: str
    reason: str


@dataclass(frozen=True)
class Dataset:
    examples: tuple[Example, ...]
    quarantine: tuple[Quarantine, ...]
    observed_feedback: int
    duplicate_feedback: int

    @property
    def excluded_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(q.reason for q in self.quarantine).items()))


# The caller supplies an independently trusted observation adapter. No default
# actor string, replayable flag, or collection permission authenticates feedback.
Verifier = Callable[[Feedback], str | None]


def _bounded(records: Iterable, expected: type) -> tuple:
    result = []
    for record in records:
        if len(result) >= MAX_RECORDS or type(record) is not expected:
            raise ValueError("invalid or excessive records")
        result.append(record)
    return tuple(result)


def build_dataset(sources: Iterable[SourceRevision], outputs: Iterable[OutputRevision],
                  feedback: Iterable[Feedback], rules: Iterable[GoldRule], *,
                  verify_actor: Verifier | None = None) -> Dataset:
    """Construct exact-revision examples; never collect, transmit, execute or deploy.

    `verify_actor` must verify an immutable event observation independently of its
    actor_id, then return the authenticated actor. Without it every row is denied.
    This function neither implements nor establishes that trust boundary.
    """
    sources = _bounded(sources, SourceRevision)
    outputs = _bounded(outputs, OutputRevision)
    feedback = _bounded(feedback, Feedback)
    rules = _bounded(rules, GoldRule)
    if sum(len(x.content) for x in sources + outputs) + sum(len(f.correction or b"") for f in feedback) > MAX_TOTAL_BYTES:
        raise ValueError("dataset byte limit exceeded")
    source_map = {}
    output_map = {}
    for record, key, table in (
        *((s, (s.source_id, s.revision), source_map) for s in sources),
        *((o, (o.output_id, o.revision), output_map) for o in outputs),
    ):
        if key in table and table[key] != record:
            raise ValueError("conflicting immutable revision")
        table[key] = record
    families = {}
    for output in outputs:
        source = source_map.get((output.source_id, output.source_revision))
        if source:
            for identity in (("source", source.source_id), ("output", output.output_id)):
                if identity in families and families[identity] != source.family_id:
                    raise ValueError("related revisions must share one family")
                families[identity] = source.family_id
    rule_map = {}
    for rule in rules:
        key = (rule.task_type, rule.rule_id)
        if key in rule_map and rule_map[key] != rule:
            raise ValueError("conflicting rule revision")
        rule_map[key] = rule
    unique = {}
    conflicting_ids = set()
    conflicting_targets = set()
    duplicates = 0
    for f in feedback:
        if f.feedback_id in unique:
            duplicates += 1
            if unique[f.feedback_id] != f:
                conflicting_ids.add(f.feedback_id)
                conflicting_targets.update(((f.output_id, f.output_revision),
                                            (unique[f.feedback_id].output_id, unique[f.feedback_id].output_revision)))
        unique[f.feedback_id] = f
    quarantine = []
    candidates = {}
    excluded_targets = conflicting_targets
    for f in sorted(unique.values(), key=lambda f: f.feedback_id):
        key = (f.output_id, f.output_revision)
        output = output_map.get(key)
        source = source_map.get((output.source_id, output.source_revision)) if output else None
        rule = rule_map.get((output.task_type, f.rule_id)) if output else None
        reason = None
        if f.feedback_id in conflicting_ids:
            reason = "conflicting_feedback_id"
        elif verify_actor is None:
            reason = "unverified_actor"
        else:
            try:
                verified = verify_actor(f)
            except Exception:
                verified = None  # unavailable authority fails closed
            if verified != f.actor_id:
                reason = "unverified_actor"
        if reason is None:
            if not f.target_available:
                reason = "unavailable_target"
            elif output is None or output.sha256 != f.output_sha256:
                reason = "missing_or_changed_output"
            elif source is None or source.sha256 != output.source_sha256:
                reason = "missing_or_changed_source"
            elif not source.complete or not output.complete:
                reason = "incomplete_context"
            elif rule is None:
                reason = "unknown_gold_rule"
            elif f.kind == "negative":
                reason = "negative_feedback"
            elif f.kind not in rule.kinds:
                reason = "unsupported_gold_kind"
        if reason:
            quarantine.append(Quarantine(f.feedback_id, reason))
            # A distinct unverified event cannot veto verified observations.
            # Conflicting reuse of an immutable ID conservatively quarantines
            # both targets before actor verification.
            if reason not in {"unverified_actor", "conflicting_feedback_id"}:
                excluded_targets.add(key)
            continue
        gold = f.correction if f.kind == "correction" else output.content
        candidates.setdefault(key, []).append((f, source, output, gold))
    examples = []
    for key, group in sorted(candidates.items()):
        if key in excluded_targets or len({(item[3], item[0].rule_id) for item in group}) != 1:
            quarantine.extend(Quarantine(item[0].feedback_id, "conflicting_feedback") for item in group)
            continue
        first = group[0]
        examples.append(Example(first[1], first[2], first[3], first[0].rule_id,
                                tuple(item[0] for item in group)))
    return Dataset(tuple(examples), tuple(sorted(quarantine, key=lambda q: q.feedback_id)),
                   len(feedback), duplicates)


def _example_identity(example: Example) -> dict:
    return {"source_id": example.source.source_id, "source_revision": example.source.revision,
            "source_sha256": example.source.sha256, "family_id": example.source.family_id,
            "output_id": example.output.output_id, "output_revision": example.output.revision,
            "output_sha256": example.output.sha256, "task_type": example.output.task_type,
            "gold_sha256": digest(example.gold), "rule_id": example.rule_id,
            "feedback": [{"feedback_id": f.feedback_id, "actor_id": f.actor_id,
                          "output_id": f.output_id, "output_revision": f.output_revision,
                          "output_sha256": f.output_sha256, "rule_id": f.rule_id,
                          "kind": f.kind, "target_available": f.target_available,
                          "correction_sha256": digest(f.correction) if f.correction is not None else None}
                         for f in example.feedback]}


def _json_digest(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


@dataclass(frozen=True)
class LockedSplit:
    """Immutable in-process holdout snapshot, not a persisted experiment registry."""
    dataset_sha256: str
    seed: str
    assignments: tuple[tuple[str, str], ...]
    sha256: str

    def partition(self, family_id: str) -> str:
        return dict(self.assignments)[family_id]

    def verify(self, dataset: Dataset) -> None:
        expected = lock_split(dataset, seed=self.seed)
        if expected != self:
            raise ValueError("dataset or locked split changed")


def lock_split(dataset: Dataset, *, seed: str) -> LockedSplit:
    """Lock a reproducible 80/20 family split before any optimizer is invoked.

    Families use SHA256(seed,family) modulo ten. Empty heldout/training partitions
    are retained explicitly; they must not be represented as valid evaluation.
    """
    _token(seed)
    if type(dataset) is not Dataset or len(dataset.examples) > MAX_RECORDS:
        raise ValueError("invalid dataset")
    identities = [_example_identity(e) for e in dataset.examples]
    identities.sort(key=lambda e: (e["output_id"], e["output_revision"]))
    assignments = tuple((family, "heldout" if int(_json_digest([seed, family]), 16) % 10 < 2 else "train")
                        for family in sorted({e.source.family_id for e in dataset.examples}))
    dataset_hash = _json_digest({"examples": identities,
                                 "quarantine": sorted((q.feedback_id, q.reason) for q in dataset.quarantine),
                                 "observed_feedback": dataset.observed_feedback,
                                 "duplicate_feedback": dataset.duplicate_feedback})
    manifest_hash = _json_digest({"version": "refinery-lineage-local.v1", "dataset_sha256": dataset_hash,
                                  "seed": seed, "assignments": assignments})
    return LockedSplit(dataset_hash, seed, assignments, manifest_hash)
