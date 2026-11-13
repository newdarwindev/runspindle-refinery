"""Exact approved candidate activation with durable intent and independent readback.

The disposable adapter executes a bounded synthetic lookup program from actual
loaded bytes. It is not a provider, workflow executor or production service.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from .evaluation import Report, _integer
from .lineage import digest, _token, _hash, _bytes
from .storage import Store, TYPES, pack, version


@dataclass(frozen=True)
class Policy:
    environment: str
    min_quality_ppm: int
    min_improvement_ppm: int
    max_cost_microunits: int
    max_latency_ms: int
    format: str = "synthetic.map.v1"

    def __post_init__(self):
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
class Approval:
    candidate_sha256: str
    evaluation_sha256: str
    policy_sha256: str
    environment: str
    actor: str
    evidence: str

    def __post_init__(self):
        for value in (
            self.candidate_sha256,
            self.evaluation_sha256,
            self.policy_sha256,
        ):
            _hash(value)
        for value in (self.environment, self.actor, self.evidence):
            _token(value)


TYPES.update({"Policy": Policy, "Approval": Approval})


class ServingAdapter(Protocol):
    def load(
        self,
        activation_id,
        environment,
        generation,
        candidate_sha256,
        content,
        predecessor_sha256,
    ): ...
    def readback(self, environment): ...


def program(content):
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


class LocalServing:
    """Atomic local loader with its own database and a real byte-driven predict."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS active(environment TEXT PRIMARY KEY, activation TEXT, generation INTEGER, digest TEXT, content BLOB)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, action TEXT, activation TEXT, digest TEXT)"
        )

    def close(self):
        self.db.close()

    def load(
        self,
        activation_id,
        environment,
        generation,
        candidate_sha256,
        content,
        predecessor_sha256,
    ):
        _token(activation_id)
        _token(environment)
        _integer(generation, positive=True)
        _hash(candidate_sha256)
        if digest(content) != candidate_sha256:
            raise ValueError("candidate bytes changed")
        program(content)  # parse/validate before replacing the prior bytes
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO calls(action,activation,digest) VALUES('load',?,?)",
                (activation_id, candidate_sha256),
            )
            old = self.db.execute(
                "SELECT activation,generation,digest,content FROM active WHERE environment=?",
                (environment,),
            ).fetchone()
            if old and old[:3] == (activation_id, generation, candidate_sha256):
                if old[3] != content:
                    raise ValueError("changed idempotent activation bytes")
            else:
                if (old[2] if old else None) != predecessor_sha256 or (
                    old and generation <= old[1]
                ):
                    raise ValueError("serving predecessor or generation conflict")
                self.db.execute(
                    "INSERT OR REPLACE INTO active VALUES(?,?,?,?,?)",
                    (environment, activation_id, generation, candidate_sha256, content),
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return True

    def readback(self, environment):
        row = self.db.execute(
            "SELECT activation,generation,digest,content FROM active WHERE environment=?",
            (environment,),
        ).fetchone()
        if not row:
            return None
        if digest(row[3]) != row[2]:
            raise ValueError("serving bytes corrupt")
        program(row[3])
        return {
            "activation_id": row[0],
            "generation": row[1],
            "candidate_sha256": row[2],
        }

    def predict(self, environment, text):
        # One read obtains both bytes and attribution; never consult registry intent.
        row = self.db.execute(
            "SELECT digest,content FROM active WHERE environment=?", (environment,)
        ).fetchone()
        if not row or digest(row[1]) != row[0]:
            raise ValueError("no verified active candidate")
        value = program(row[1])
        if text not in value["responses"]:
            raise ValueError("unsupported synthetic input")
        return {"output": value["responses"][text], "candidate_sha256": row[0]}


class Registry:
    def __init__(self, store: Store, *, verify_approval=None):
        self.store, self.verify_approval = store, verify_approval

    def qualify(self, api_version, experiment_id, policy: Policy):
        version(api_version)
        policy.__post_init__()
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
            or report.candidate_score * 1_000_000 < policy.min_quality_ppm
            or (report.candidate_score - report.baseline_score) * 1_000_000
            < policy.min_improvement_ppm
            or usage.get("unresolved_calls") != 0
            or usage.get("reason") is not None
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

    def state(self, environment):
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
        approval.__post_init__()
        candidate = self.store.get("candidate", candidate_key)
        if (
            digest(pack(candidate)) != candidate_key
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
            actor = self.verify_approval(approval) if self.verify_approval else None
        except Exception:
            actor = None
        if actor != approval.actor:
            raise ValueError("approval denied")
        environment = approval.environment
        with self.store.transaction() as tx:
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

    def _intent(self, activation_id):
        _token(activation_id)
        row = self.store.db.execute(
            "SELECT environment,generation,candidate,predecessor,state FROM activations WHERE identity=?",
            (activation_id,),
        ).fetchone()
        if not row:
            raise ValueError("unknown activation")
        return row

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
        # Claim dispatch durably. A second caller must read back; never reload unknown.
        with self.store.transaction() as tx:
            changed = tx.execute(
                "UPDATE activations SET state='unknown' WHERE identity=? AND state='intent'",
                (activation_id,),
            ).rowcount
        if not changed:
            return self.reconcile(api_version, activation_id, adapter)
        checkpoint("before_candidate_load")
        candidate = self.store.get("candidate", key)
        old_digest = (
            self.store.get("candidate", predecessor)["candidate_sha256"]
            if predecessor
            else None
        )
        try:
            accepted = adapter.load(
                activation_id,
                environment,
                generation,
                candidate["candidate_sha256"],
                candidate["content"],
                old_digest,
            )
        except Exception:
            # A thrown exception may follow an actual load: keep unknown until readback.
            accepted = None
        checkpoint("after_candidate_load")
        return self.reconcile(
            api_version, activation_id, adapter, rejected=accepted is False
        )

    def reconcile(
        self, api_version, activation_id, adapter: ServingAdapter, *, rejected=False
    ):
        version(api_version)
        environment, generation, key, predecessor, state = self._intent(activation_id)
        if state in {"active", "failed"}:
            return state
        try:
            observed = adapter.readback(environment)
        except Exception:
            observed = "unavailable"
        expected = {
            "activation_id": activation_id,
            "generation": generation,
            "candidate_sha256": self.store.get("candidate", key)["candidate_sha256"],
        }
        old_digest = (
            self.store.get("candidate", predecessor)["candidate_sha256"]
            if predecessor
            else None
        )
        prior_observed = (
            observed is None
            if predecessor is None
            else (
                type(observed) is dict
                and observed.get("candidate_sha256") == old_digest
            )
        )
        outcome = (
            "active"
            if observed == expected
            else "failed" if rejected and prior_observed else "unknown"
        )
        with self.store.transaction() as tx:
            if self.state(environment)["pending"] != activation_id:
                raise ValueError("activation ownership conflict")
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
