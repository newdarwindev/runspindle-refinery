"""Exact approved candidate activation with durable intent and independent readback.

The disposable adapter executes a bounded synthetic lookup program from actual
loaded bytes. It is not a provider, workflow executor or production service.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol, TypedDict, cast

from .evaluation import Report, _integer
from .lineage import MAX_BYTES, MAX_RECORDS, _bytes, _hash, _token, digest
from .storage import TYPES, Store, pack, version


@dataclass(frozen=True)
class Policy:
    environment: str
    min_quality_ppm: int
    min_improvement_ppm: int
    max_cost_microunits: int
    max_latency_ms: int
    format: str = "synthetic.map.v1"

    def __post_init__(self) -> None:
        _token(self.environment)
        for name in (
            "min_quality_ppm",
            "min_improvement_ppm",
            "max_cost_microunits",
            "max_latency_ms",
        ):
            _integer(getattr(self, name))
        if (
            max(self.min_quality_ppm, self.min_improvement_ppm) > 1_000_000
            or self.format != "synthetic.map.v1"
        ):
            raise ValueError("unsupported serving policy")


@dataclass(frozen=True)
class SamplePolicy(Policy):
    """Separate stored type; legacy Policy bytes and approvals stay unchanged."""

    min_heldout_cases: int = 1

    def __post_init__(self) -> None:
        super().__post_init__()
        _integer(self.min_heldout_cases, positive=True)
        if self.min_heldout_cases > MAX_RECORDS:
            raise ValueError("unsupported heldout sample bound")


@dataclass(frozen=True)
class Approval:
    candidate_sha256: str
    evaluation_sha256: str
    policy_sha256: str
    environment: str
    actor: str
    evidence: str

    def __post_init__(self) -> None:
        for value in (
            self.candidate_sha256,
            self.evaluation_sha256,
            self.policy_sha256,
        ):
            _hash(value)
        for value in (self.environment, self.actor, self.evidence):
            _token(value)


TYPES.update({"Policy": Policy, "SamplePolicy": SamplePolicy, "Approval": Approval})


class ServingIdentity(TypedDict):
    activation_id: str
    generation: int
    candidate_sha256: str


class ServingAdapter(Protocol):
    def load(
        self,
        activation_id: str,
        environment: str,
        generation: int,
        candidate_sha256: str,
        content: bytes,
        predecessor: ServingIdentity | None,
        /,
    ) -> bool: ...
    def readback(self, environment: str) -> Any: ...


def program(content: bytes) -> Any:
    _bytes(content)
    value = json.loads(content)
    if (
        type(value) is not dict
        or set(value) != {"format", "responses"}
        or value["format"] != "synthetic.map.v1"
        or type(value["responses"]) is not dict
        or not 1 <= len(value["responses"]) <= 1000
    ):
        raise ValueError("incompatible candidate program")
    for key, result in value["responses"].items():
        if (
            type(key) is not str
            or type(result) is not str
            or len(key.encode()) > 4096
            or len(result.encode()) > 4096
        ):
            raise ValueError("unbounded candidate program")
    return value


LOCAL_STATEMENTS = (
    "CREATE TABLE active(environment TEXT PRIMARY KEY, activation TEXT, generation INTEGER, digest TEXT, content BLOB)",
    "CREATE TABLE calls(id INTEGER PRIMARY KEY, action TEXT, activation TEXT, digest TEXT)",
)


def _local_shape(db: sqlite3.Connection) -> Any:
    return list(
        db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    )


def _validate_local_schema(db: sqlite3.Connection) -> None:
    """Exact legacy layout, not a migration or hostile-SQLite-file validator."""
    if db.execute("PRAGMA user_version").fetchone() != (0,) or db.execute(
        "SELECT count(*) FROM sqlite_temp_master"
    ).fetchone() != (0,):
        raise ValueError("incompatible local serving schema")
    expected = sqlite3.connect(":memory:")
    try:
        for statement in LOCAL_STATEMENTS:
            expected.execute(statement)
        if _local_shape(db) != _local_shape(expected):
            raise ValueError("incompatible local serving schema")
    finally:
        expected.close()


class LocalServing:
    """Atomic local loader with its own database and a real byte-driven predict."""

    def __init__(self, path: Any) -> None:
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            # Only an entirely empty v0 schema can initialize. Partial existing
            # stores are never repaired and existing version zero is preserved.
            if not _local_shape(self.db):
                if self.db.execute("PRAGMA user_version").fetchone() != (0,):
                    raise ValueError("incompatible local serving schema")
                for statement in LOCAL_STATEMENTS:
                    self.db.execute(statement)
            _validate_local_schema(self.db)
            self.db.commit()
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            _validate_local_schema(self.db)
            yield
            _validate_local_schema(self.db)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self) -> None:
        self.db.close()

    def _active_row(self, environment: str) -> Any:
        """Read bounded exact persisted identity/bytes inside the caller's transaction.

        The SQL projection never materializes an oversized or non-BLOB program.
        Invalid retained state blocks observation and replacement, not repairs it.
        """
        _token(environment)
        row = self.db.execute(
            "SELECT CASE WHEN typeof(activation)='text' "
            "AND length(CAST(activation AS BLOB)) BETWEEN 1 AND 128 THEN activation END AS activation, "
            "CASE WHEN typeof(generation)='integer' THEN generation END, "
            "CASE WHEN typeof(digest)='text' AND length(CAST(digest AS BLOB))=64 THEN digest END, "
            "CASE WHEN typeof(content)='blob' AND length(content) BETWEEN 1 AND ? "
            "THEN content ELSE NULL END FROM active WHERE environment=?",
            (MAX_BYTES, environment),
        ).fetchone()
        if row is None:
            return None
        _token(row[0])
        _integer(row[1], positive=True)
        _hash(row[2])
        _bytes(row[3])
        if digest(row[3]) != row[2]:
            raise ValueError("serving bytes corrupt")
        program(row[3])
        return row

    def load(
        self,
        activation_id: str,
        environment: str,
        generation: int,
        candidate_sha256: str,
        content: bytes,
        predecessor_sha256: ServingIdentity | str | None,
    ) -> bool:
        _token(activation_id)
        _token(environment)
        _integer(generation, positive=True)
        _hash(candidate_sha256)
        if digest(content) != candidate_sha256:
            raise ValueError("candidate bytes changed")
        program(content)  # parse/validate before replacing the prior bytes
        predecessor = predecessor_sha256
        if type(predecessor) is dict:
            if not _readback_matches(predecessor, predecessor):
                raise ValueError("invalid serving predecessor")
            _token(predecessor["activation_id"])
            _integer(predecessor["generation"], positive=True)
            _hash(predecessor["candidate_sha256"])
            predecessor = ServingIdentity(
                activation_id=predecessor["activation_id"],
                generation=predecessor["generation"],
                candidate_sha256=predecessor["candidate_sha256"],
            )
        elif predecessor is not None:
            _hash(predecessor)
        with self._transaction():
            self.db.execute(
                "INSERT INTO calls(action,activation,digest) VALUES('load',?,?)",
                (activation_id, candidate_sha256),
            )
            old = self._active_row(environment)
            if old and old[:3] == (activation_id, generation, candidate_sha256):
                if old[3] != content:
                    raise ValueError("changed idempotent activation bytes")
            else:
                if not _predecessor_matches(old, predecessor) or (
                    old and generation <= old[1]
                ):
                    raise ValueError("serving predecessor or generation conflict")
                self.db.execute(
                    "INSERT OR REPLACE INTO active VALUES(?,?,?,?,?)",
                    (environment, activation_id, generation, candidate_sha256, content),
                )
        return True

    def readback(self, environment: str) -> Any:
        with self._transaction():
            row = self._active_row(environment)
        if row is None:
            return None
        return {
            "activation_id": row[0],
            "generation": row[1],
            "candidate_sha256": row[2],
        }

    def predict(self, environment: str, text: str) -> Any:
        # One read obtains both bytes and attribution; never consult registry intent.
        with self._transaction():
            row = self._active_row(environment)
        if row is None:
            raise ValueError("no verified active candidate")
        value = program(row[3])
        if text not in value["responses"]:
            raise ValueError("unsupported synthetic input")
        return {"output": value["responses"][text], "candidate_sha256": row[2]}


def _readback_matches(observed: Any, expected: Any) -> bool:
    """Compare only exact wire-shaped primitives; never invoke foreign equality."""
    return (
        type(observed) is dict
        and all(type(key) is str for key in observed)
        and set(observed) == {"activation_id", "generation", "candidate_sha256"}
        and type(observed["activation_id"]) is str
        and type(observed["generation"]) is int
        and type(observed["candidate_sha256"]) is str
        and observed == expected
    )


def _predecessor_matches(row: Any, expected: ServingIdentity | str | None) -> bool:
    if row is None:
        return expected is None
    if type(expected) is str:
        # Keep the direct synthetic loader's historical digest-only API.
        return bool(row[2] == expected)
    return expected is not None and _readback_matches(
        {
            "activation_id": row[0],
            "generation": row[1],
            "candidate_sha256": row[2],
        },
        expected,
    )


def _score_counts(report: Report) -> tuple[int, int, int]:
    """Decode canonical exact-match ratios, not new evidence authentication.

    Reports retain their existing float fields and immutable encoded bytes.
    Only an exact k/N emitted by the evaluator is accepted; no tolerance or
    rounding changes eligibility. N counts held-out identities, never retries.
    """
    cases = report.heldout_cases
    if type(cases) is not tuple or not 1 <= len(cases) <= MAX_RECORDS:
        raise ValueError("invalid heldout denominator")
    seen: set[tuple[str, str]] = set()
    for case in cases:
        if (
            type(case) is not tuple
            or len(case) != 2
            or any(type(value) is not str for value in case)
        ):
            raise ValueError("invalid heldout identity")
        for value in case:
            _token(value)
        if case in seen:
            raise ValueError("duplicate heldout identity")
        seen.add(case)
    denominator = len(cases)
    counts: list[int] = []
    for score in (report.baseline_score, report.candidate_score):
        if type(score) is not float or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("invalid exact-match score")
        for correct in range(denominator + 1):
            if correct / denominator == score:
                counts.append(correct)
                break
        else:
            raise ValueError("noncanonical exact-match score")
    return counts[0], counts[1], denominator


class Registry:
    def __init__(self, store: Store, *, verify_approval=None):
        self.store, self.verify_approval = store, verify_approval

    def qualify(self, api_version, experiment_id, policy: Policy):
        version(api_version)
        if type(policy) not in (Policy, SamplePolicy):
            raise ValueError("unsupported serving policy type")
        type(policy).__post_init__(policy)
        experiment = self.store.get("experiment", experiment_id)
        row = self.store.db.execute(
            "SELECT state,report FROM experiments WHERE identity=?", (experiment_id,)
        ).fetchone()
        if not row or row[0] != "complete":
            raise ValueError("evaluation is not durably complete")
        report_sha = row[1]
        report = self.store.get("report", report_sha)
        if type(report) is not Report or digest(pack(report)) != report_sha:
            raise ValueError("evaluation evidence changed")
        usage = dict(report.usage)
        if (
            report.status != "passed"
            or report.reason is not None
            or report.safety is not True
            or report.baseline_score is None
            or report.candidate_score is None
            or not report.heldout_cases
            or not report.attempts
            or usage.get("unresolved_calls") != 0
            or usage.get("reason") is not None
        ):
            raise ValueError("evaluation policy gate blocked")
        baseline, candidate_count, denominator = _score_counts(report)
        if isinstance(policy, SamplePolicy) and denominator < policy.min_heldout_cases:
            raise ValueError("insufficient heldout sample count")
        if (
            candidate_count * 1_000_000 < policy.min_quality_ppm * denominator
            or (candidate_count - baseline) * 1_000_000
            < policy.min_improvement_ppm * denominator
        ):
            raise ValueError("evaluation policy gate blocked")
        for field, ceiling in (
            ("cost_microunits", policy.max_cost_microunits),
            ("elapsed_ms", policy.max_latency_ms),
        ):
            _integer(usage.get(field))
            if usage[field] > ceiling:
                raise ValueError("evaluation resource gate blocked")
        if (
            report.dataset_sha256,
            report.split_sha256,
            report.baseline_sha256,
            report.candidate_sha256,
            report.config_sha256,
        ) != (
            experiment.dataset_sha256,
            experiment.split_sha256,
            experiment.baseline_sha256,
            experiment.candidate_sha256,
            experiment.config_sha256,
        ):
            raise ValueError("evaluation artifact bindings changed")
        experiment.__post_init__()
        program(experiment.candidate)
        policy_sha = digest(pack(policy))
        candidate = {
            "content": experiment.candidate,
            "candidate_sha256": experiment.candidate_sha256,
            "evaluation_sha256": report_sha,
            "policy_sha256": policy_sha,
            "environment": policy.environment,
        }
        key = digest(pack(candidate))
        with self.store.transaction() as tx:
            self.store.put(tx, "policy", policy_sha, policy)
            self.store.put(tx, "candidate", key, candidate)
        return key

    def state(self, environment: str) -> Any:
        _token(environment)
        row = self.store.db.execute(
            "SELECT generation,active,pending FROM environments WHERE identity=?",
            (environment,),
        ).fetchone()
        return (
            {"generation": row[0], "active": row[1], "pending": row[2]}
            if row
            else {"generation": 0, "active": None, "pending": None}
        )

    def observe(
        self, api_version: str, environment: str, adapter: ServingAdapter
    ) -> dict[str, Any]:
        """Observe current loader state without changing historical activation outcomes.

        The trusted readback is not an authority grant or an atomic cross-store
        guarantee. Registry changes during that callback reject the observation.
        """
        cast(Callable[[str], None], version)(api_version)
        _token(environment)

        def binding() -> tuple[Any, Any, Any, Any]:
            state = self.state(environment)
            _integer(state["generation"])
            expected, candidate_bytes = None, None
            if state["active"] is not None:
                key = state["active"]
                _hash(key)
                candidate = self.store.get("candidate", key)
                candidate_bytes = pack(candidate)
                if (
                    digest(candidate_bytes) != key
                    or candidate["environment"] != environment
                    or digest(candidate["content"]) != candidate["candidate_sha256"]
                ):
                    raise ValueError("active candidate identity changed")
                program(candidate["content"])
                active = self.store.db.execute(
                    "SELECT identity,generation FROM activations WHERE environment=? "
                    "AND candidate=? AND state='active' ORDER BY generation DESC LIMIT 1",
                    (environment, key),
                ).fetchone()
                if active is None:
                    raise ValueError("active activation unavailable")
                _token(active[0])
                _integer(active[1], positive=True)
                if active[1] > state["generation"]:
                    raise ValueError("active generation changed")
                expected = {
                    "activation_id": active[0],
                    "generation": active[1],
                    "candidate_sha256": candidate["candidate_sha256"],
                }
            pending = None
            if state["pending"] is not None:
                pending = self._intent(state["pending"])
                if (
                    pending[:2] != (environment, state["generation"])
                    or pending[3] != state["active"]
                    or pending[4] not in {"intent", "unknown"}
                ):
                    raise ValueError("pending activation identity changed")
            return state, expected, candidate_bytes, pending

        with self.store.transaction():
            captured = binding()
        state, expected, _, _ = captured
        available = True
        try:
            observed = adapter.readback(environment)
            if observed is not None:
                if not _readback_matches(observed, observed):
                    raise ValueError("invalid readback shape")
                _token(observed["activation_id"])
                _integer(observed["generation"], positive=True)
                _hash(observed["candidate_sha256"])
                observed = dict(observed)
        except Exception:
            available, observed = False, None
        with self.store.transaction():
            if binding() != captured:
                raise ValueError("registry changed during observation")
        status = (
            "unavailable"
            if not available
            else "pending"
            if state["pending"] is not None
            else "empty"
            if expected is None and observed is None
            else "current"
            if expected is not None and _readback_matches(observed, expected)
            else "drift"
        )
        return {
            "version": "runspindle.serving-observation.v1",
            "environment": environment,
            "generation": state["generation"],
            "active_candidate": state["active"],
            "pending_activation": state["pending"],
            "candidate_sha256": expected["candidate_sha256"] if expected else None,
            "observed_candidate_sha256": observed["candidate_sha256"]
            if observed
            else None,
            "status": status,
        }

    def begin(
        self,
        api_version,
        activation_id,
        candidate_key,
        approval: Approval,
        *,
        expected_generation,
    ):
        version(api_version)
        _token(activation_id)
        _hash(candidate_key)
        _integer(expected_generation)
        if type(approval) is not Approval:
            raise ValueError("unsupported approval type")
        Approval.__post_init__(approval)
        store, verifier = self.store, self.verify_approval
        approval_bytes, expected_actor = pack(approval), approval.actor
        candidate = store.get("candidate", candidate_key)
        candidate_bytes = pack(candidate)
        if (
            digest(candidate_bytes) != candidate_key
            or digest(candidate["content"]) != candidate["candidate_sha256"]
        ):
            raise ValueError("candidate identity changed")
        if (
            approval.candidate_sha256,
            approval.evaluation_sha256,
            approval.policy_sha256,
            approval.environment,
        ) != (
            candidate["candidate_sha256"],
            candidate["evaluation_sha256"],
            candidate["policy_sha256"],
            candidate["environment"],
        ):
            raise ValueError("approval binding conflict")
        try:
            actor = verifier(approval) if verifier else None
        except Exception:
            actor = None
        if (
            type(actor) is not str
            or actor != expected_actor
            or self.store is not store
            or self.verify_approval is not verifier
            or pack(approval) != approval_bytes
        ):
            raise ValueError("approval denied")
        environment = candidate["environment"]
        with store.transaction() as tx:
            if pack(store.get("candidate", candidate_key)) != candidate_bytes:
                raise ValueError("candidate changed during approval")
            state = self.state(environment)
            if (
                state["generation"] != expected_generation
                or state["pending"] is not None
            ):
                raise ValueError("promotion compare-and-swap conflict")
            if state["active"] == candidate_key:
                raise ValueError("candidate already active")
            self.store.room(tx, "activations")
            evidence = digest(pack(approval))
            self.store.put(tx, "approval", evidence, approval)
            tx.execute(
                "INSERT INTO activations VALUES(?,?,?,?,?,'intent',?)",
                (
                    activation_id,
                    environment,
                    expected_generation + 1,
                    candidate_key,
                    state["active"],
                    evidence,
                ),
            )
            tx.execute(
                "INSERT OR REPLACE INTO environments VALUES(?,?,?,?)",
                (environment, expected_generation + 1, state["active"], activation_id),
            )
        return activation_id

    def _intent(self, activation_id: str) -> Any:
        _token(activation_id)
        row = self.store.db.execute(
            "SELECT environment,generation,candidate,predecessor,state FROM activations WHERE identity=?",
            (activation_id,),
        ).fetchone()
        if not row:
            raise ValueError("unknown activation")
        return row

    def _dispatch_predecessor(
        self, environment: str, generation: int, key: str | None
    ) -> ServingIdentity | None:
        if key is None:
            return None
        _hash(key)
        candidate = self.store.get("candidate", key)
        if (
            digest(pack(candidate)) != key
            or candidate["environment"] != environment
            or digest(candidate["content"]) != candidate["candidate_sha256"]
        ):
            raise ValueError("dispatch predecessor binding changed")
        row = self.store.db.execute(
            "SELECT identity,generation FROM activations WHERE environment=? "
            "AND candidate=? AND state='active' AND generation<? "
            "ORDER BY generation DESC LIMIT 1",
            (environment, key, generation),
        ).fetchone()
        if row is None:
            raise ValueError("dispatch predecessor activation unavailable")
        _token(row[0])
        _integer(row[1], positive=True)
        return {
            "activation_id": row[0],
            "generation": row[1],
            "candidate_sha256": candidate["candidate_sha256"],
        }

    def _authorize_load(self, activation_id: str, expected: Any) -> Any:
        """Fresh approval at dispatch, not perpetual authority from stored intent.

        The verifier is a trusted injected authority. Read immutable bindings and
        mutable ownership again after its callback, before returning exact bytes.
        This is not an atomic transaction with an external serving target.
        """
        environment, generation, key, predecessor, _ = expected
        ownership = {
            "generation": generation,
            "active": predecessor,
            "pending": activation_id,
        }
        if (
            self._intent(activation_id) != expected
            or self.state(environment) != ownership
        ):
            raise ValueError("activation ownership changed")
        row = self.store.db.execute(
            "SELECT approval FROM activations WHERE identity=?", (activation_id,)
        ).fetchone()
        if row is None:
            raise ValueError("activation approval unavailable")
        approval_key = row[0]
        approval = self.store.get("approval", approval_key)
        candidate = self.store.get("candidate", key)
        approval_bytes, candidate_bytes = pack(approval), pack(candidate)
        if type(approval) is not Approval:
            raise ValueError("activation approval type")
        approval.__post_init__()
        if (
            digest(approval_bytes) != approval_key
            or digest(candidate_bytes) != key
            or digest(candidate["content"]) != candidate["candidate_sha256"]
            or (
                approval.candidate_sha256,
                approval.evaluation_sha256,
                approval.policy_sha256,
                approval.environment,
            )
            != (
                candidate["candidate_sha256"],
                candidate["evaluation_sha256"],
                candidate["policy_sha256"],
                candidate["environment"],
            )
            or approval.environment != environment
        ):
            raise ValueError("activation approval binding changed")
        prior = self._dispatch_predecessor(environment, generation, predecessor)
        expected_actor = approval.actor
        try:
            actor = self.verify_approval(approval) if self.verify_approval else None
        except Exception:
            actor = None
        if (
            type(actor) is not str
            or actor != expected_actor
            or pack(approval) != approval_bytes
        ):
            raise ValueError("activation approval denied")
        if (
            self._intent(activation_id) != expected
            or self.state(environment) != ownership
            or self.store.db.execute(
                "SELECT approval FROM activations WHERE identity=?", (activation_id,)
            ).fetchone()
            != (approval_key,)
            or pack(self.store.get("approval", approval_key)) != approval_bytes
            or pack(self.store.get("candidate", key)) != candidate_bytes
            or self._dispatch_predecessor(environment, generation, predecessor) != prior
        ):
            raise ValueError("activation binding changed during approval")
        return candidate, prior

    def load(
        self,
        api_version,
        activation_id,
        adapter: ServingAdapter,
        *,
        checkpoint=lambda _: None,
    ):
        version(api_version)
        environment, generation, key, predecessor, state = self._intent(activation_id)
        if state != "intent":
            return self.reconcile(api_version, activation_id, adapter)
        self._authorize_load(
            activation_id, (environment, generation, key, predecessor, state)
        )
        # Claim dispatch durably. A second caller must read back; never reload unknown.
        with self.store.transaction() as tx:
            changed = tx.execute(
                "UPDATE activations SET state='unknown' WHERE identity=? AND state='intent'",
                (activation_id,),
            ).rowcount
        if not changed:
            return self.reconcile(api_version, activation_id, adapter)
        checkpoint("before_candidate_load")
        candidate, prior = self._authorize_load(
            activation_id, (environment, generation, key, predecessor, "unknown")
        )
        try:
            accepted = adapter.load(
                activation_id,
                environment,
                generation,
                candidate["candidate_sha256"],
                candidate["content"],
                prior,
            )
        except Exception:
            # A thrown exception may follow an actual load: keep unknown until readback.
            accepted = None
        checkpoint("after_candidate_load")
        return self._reconcile(
            api_version, activation_id, adapter, rejected=accepted is False
        )

    def reconcile(
        self, api_version, activation_id, adapter: ServingAdapter, *, rejected=False
    ):
        if rejected is not False:
            raise ValueError("only the dispatching load may report a rejection")
        return self._reconcile(api_version, activation_id, adapter, rejected=False)

    def _reconcile(
        self,
        api_version: str,
        activation_id: str,
        adapter: ServingAdapter,
        *,
        rejected: bool,
    ) -> str:
        cast(Callable[[str], None], version)(api_version)
        environment, generation, key, predecessor, state = self._intent(activation_id)
        if state in {"active", "failed"}:
            return cast(str, state)
        if state not in {"intent", "unknown"}:
            raise ValueError("invalid activation lifecycle state")
        intent = (environment, generation, key, predecessor, state)
        ownership = {
            "generation": generation,
            "active": predecessor,
            "pending": activation_id,
        }
        if self.state(environment) != ownership:
            raise ValueError("activation ownership conflict")
        # An approval intent is not a dispatch. Readback before the durable
        # claim must not adopt bytes or release the still-unused reservation.
        if state == "intent":
            return "intent"
        candidate = self.store.get("candidate", key)
        candidate_bytes = pack(candidate)
        if digest(candidate_bytes) != key:
            raise ValueError("candidate identity changed")
        expected = {
            "activation_id": activation_id,
            "generation": generation,
            "candidate_sha256": candidate["candidate_sha256"],
        }
        prior = self.store.db.execute(
            "SELECT identity,generation FROM activations WHERE environment=? AND candidate=? AND state='active' AND generation<? ORDER BY generation DESC LIMIT 1",
            (environment, predecessor, generation),
        ).fetchone()
        prior_candidate = (
            self.store.get("candidate", predecessor) if predecessor else None
        )
        prior_bytes = pack(prior_candidate)
        if predecessor and digest(prior_bytes) != predecessor:
            raise ValueError("predecessor identity changed")
        prior_expected = (
            {
                "activation_id": prior[0],
                "generation": prior[1],
                "candidate_sha256": prior_candidate["candidate_sha256"],
            }
            if prior and prior_candidate
            else None
        )
        try:
            observed = adapter.readback(environment)
        except Exception:
            observed = "unavailable"
        prior_observed = (
            observed is None
            if predecessor is None
            else prior_expected is not None
            and _readback_matches(observed, prior_expected)
        )
        outcome = (
            "active"
            if _readback_matches(observed, expected)
            else "failed"
            if rejected is True and prior_observed
            else "unknown"
        )
        with self.store.transaction() as tx:
            # Readback is an external callback. It cannot change the intent whose
            # observation is being committed, nor substitute immutable bindings.
            if (
                self._intent(activation_id) != intent
                or self.state(environment) != ownership
                or pack(self.store.get("candidate", key)) != candidate_bytes
                or pack(
                    self.store.get("candidate", predecessor) if predecessor else None
                )
                != prior_bytes
                or self.store.db.execute(
                    "SELECT identity,generation FROM activations WHERE environment=? AND candidate=? AND state='active' AND generation<? ORDER BY generation DESC LIMIT 1",
                    (environment, predecessor, generation),
                ).fetchone()
                != prior
            ):
                raise ValueError(
                    "activation ownership or binding changed during readback"
                )
            tx.execute(
                "UPDATE activations SET state=? WHERE identity=?",
                (outcome, activation_id),
            )
            if outcome in {"active", "failed"}:
                tx.execute(
                    "UPDATE environments SET active=?,pending=NULL WHERE identity=? AND pending=?",
                    (
                        key if outcome == "active" else predecessor,
                        environment,
                        activation_id,
                    ),
                )
        return outcome

    def rollback(self, api_version, activation_id, environment, *, expected_generation):
        version(api_version)
        state = self.state(environment)
        if (
            state["generation"] != expected_generation
            or state["pending"] is not None
            or state["active"] is None
        ):
            raise ValueError("rollback ownership or history unavailable")
        row = self.store.db.execute(
            "SELECT predecessor FROM activations WHERE environment=? AND candidate=? AND state='active' ORDER BY generation DESC LIMIT 1",
            (environment, state["active"]),
        ).fetchone()
        if not row or not row[0]:
            raise ValueError("no approved predecessor")
        predecessor = row[0]
        candidate = self.store.get("candidate", predecessor)
        if digest(candidate["content"]) != candidate["candidate_sha256"]:
            raise ValueError("rollback bytes changed")
        program(candidate["content"])
        # Use previously independently authenticated evidence, still bound to the environment.
        approved = self.store.db.execute(
            "SELECT approval FROM activations WHERE environment=? AND candidate=? AND state='active' ORDER BY generation DESC LIMIT 1",
            (environment, predecessor),
        ).fetchone()
        if not approved:
            raise ValueError("predecessor approval unavailable")
        approval = self.store.get("approval", approved[0])
        return self.begin(
            api_version,
            activation_id,
            predecessor,
            approval,
            expected_generation=expected_generation,
        )
