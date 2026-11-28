"""Durable wrappers around the existing evaluation and budget transition library."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import nullcontext
from typing import Any, Callable, cast

from .evaluation import (
    VERSION as EVALUATION_VERSION,
)
from .evaluation import (
    Attempt,
    Budget,
    Experiment,
    Halt,
    Limits,
    Report,
    Reservation,
    _integer,
    _validate_dataset,
    evaluate,
    evaluation_cases,
    evaluation_request,
    validate_provider,
)
from .feedback_use import RetainedFeedbackUse, use_guard
from .lineage import Dataset, Example, LockedSplit, _hash, _token, digest
from .storage import VERSION, Store, pack, unpack, version


def _state(budget):
    return {
        "limits": budget.limits,
        "start": budget._start,
        "last": budget._last,
        "pending": {str(k): v for k, v in budget._pending.items()},
        "owners": getattr(budget, "_owners", {}),
        "next": budget._next,
        "calls": budget.calls,
        "tokens": budget.tokens,
        "cost": budget.cost_microunits,
        "reason": budget.reason,
        "time_unknown": budget._time_unknown,
    }


def _restore(value: Any, clock: Callable[[], int]) -> Budget:
    budget = Budget.__new__(Budget)
    budget.limits, budget.clock_ms = value["limits"], clock
    budget._start, budget._last = value["start"], value["last"]
    budget._pending = {int(k): v for k, v in value["pending"].items()}
    budget._owners = value.get("owners", {})
    if type(budget._owners) is not dict:
        raise ValueError("invalid reservation ownership")
    for ticket, proof in budget._owners.items():
        if (
            type(ticket) is not str
            or not ticket.isascii()
            or not ticket.isdecimal()
            or str(int(ticket)) != ticket
            or int(ticket) not in budget._pending
        ):
            raise ValueError("invalid reservation ownership")
        _hash(proof)
    budget._next = value["next"]
    budget.calls, budget.tokens, budget.cost_microunits = (
        value["calls"],
        value["tokens"],
        value["cost"],
    )
    budget.reason, budget._time_unknown = value["reason"], value["time_unknown"]
    budget._lock = threading.Lock()
    return budget


class SQLiteBudget(Budget):
    """One shared ledger, transactional across processes. Clock must survive restart.

    A reserved call is durable before invocation; opening an experiment again is
    prohibited, even if its caller died before invoking. Unknown is never refunded.
    """

    def __init__(
        self,
        store: Store,
        identity,
        limits: Limits,
        *,
        clock_ms,
        experiment=None,
        checkpoint=lambda _: None,
    ):
        _token(identity)
        if experiment is not None:
            _token(experiment)
        self.store, self.identity, self.limits = store, identity, limits
        self.clock_ms, self.experiment, self.checkpoint = (
            clock_ms,
            experiment,
            checkpoint,
        )
        # Admission can share the caller's claim transaction, just like Store.get.
        with (
            nullcontext(store.db)
            if store.db.in_transaction
            else store.transaction() as tx
        ):
            row = tx.execute(
                "SELECT payload FROM budgets WHERE identity=?", (identity,)
            ).fetchone()
            if row:
                if unpack(row[0])["limits"] != limits:
                    raise ValueError("durable budget configuration conflict")
            else:
                store.room(tx, "budgets")
                value = _state(Budget(limits, clock_ms=clock_ms))
                tx.execute("INSERT INTO budgets VALUES(?,?)", (identity, pack(value)))

    def _ownership_proof(self, ticket: int, maximum: Any, request: Any) -> str:
        return digest(
            pack(
                (
                    "budget_reservation_owner.v1",
                    self.identity,
                    ticket,
                    self.experiment,
                    maximum,
                    request,
                )
            )
        )

    def _settlement_owner(
        self,
        tx: sqlite3.Connection,
        budget: Budget,
        ticket: int,
        receipt: Any,
        tokens: Any,
        cost: Any,
    ) -> None:
        """Require exact ledger/owner proof before changing a reservation.

        Legacy pending without proof stays charged; ticket numbers alone never
        identify an owner. Ownerless reservations explicitly bind owner None.
        """
        row = None
        if self.experiment is not None:
            row = tx.execute(
                "SELECT request,outcome FROM attempts WHERE experiment=? AND ticket=?",
                (self.experiment, ticket),
            ).fetchone()
        request = row[0] if row else None
        expected = self._ownership_proof(ticket, budget._pending.get(ticket), request)
        if budget._owners.get(str(ticket)) != expected:
            raise ValueError("reservation ownership conflict")
        if self.experiment is not None and (
            row is None
            or row[1] != "unknown"
            or type(receipt) is not Attempt
            or pack(receipt.request) != request
            or receipt.tokens != tokens
            or receipt.cost_microunits != cost
        ):
            raise ValueError("reservation attempt conflict")

    def _apply(self, method: str, *args: Any, **kwargs: Any) -> Any:
        witness = kwargs.pop("gepa_witness", None)
        witness_bytes = pack(witness)
        failure = None
        result = None
        owner = (self.identity, self.experiment)
        arguments = pack((args, kwargs))
        store, clock = self.store, self.clock_ms
        limits = pack(self.limits)
        with store.transaction() as tx:
            row = tx.execute(
                "SELECT payload FROM budgets WHERE identity=?", (self.identity,)
            ).fetchone()
            budget = _restore(unpack(row[0]), self.clock_ms)
            attempt_before = None
            if method == "settle":
                self._settlement_owner(
                    tx,
                    budget,
                    args[0],
                    kwargs.get("receipt"),
                    kwargs.get("tokens"),
                    kwargs.get("cost_microunits"),
                )
                if self.experiment is not None:
                    attempt_before = tx.execute(
                        "SELECT request,outcome,receipt FROM attempts WHERE experiment=? AND ticket=?",
                        (self.experiment, args[0]),
                    ).fetchone()
            try:
                if method == "reserve" and budget.calls >= 4096:
                    budget.stop("attempt_limit")
                    raise Halt("attempt_limit")
                result = getattr(budget, method)(*args, **kwargs)
            except Halt as exc:
                failure = exc
            # Clock callbacks must not redirect or overwrite a newer ledger,
            # mutate settlement evidence, or replace the retained attempt.
            if (
                (self.identity, self.experiment) != owner
                or self.store is not store
                or self.clock_ms is not clock
                or pack(self.limits) != limits
                or pack((args, kwargs)) != arguments
                or pack(witness) != witness_bytes
                or tx.execute(
                    "SELECT payload FROM budgets WHERE identity=?", (owner[0],)
                ).fetchone()
                != row
                or (
                    attempt_before is not None
                    and tx.execute(
                        "SELECT request,outcome,receipt FROM attempts WHERE experiment=? AND ticket=?",
                        (owner[1], args[0]),
                    ).fetchone()
                    != attempt_before
                )
            ):
                raise ValueError("budget binding changed during operation")
            if method == "reserve" and failure is None:
                budget._owners[str(result)] = self._ownership_proof(
                    cast(int, result),
                    args[0],
                    pack(kwargs.get("request"))
                    if self.experiment is not None
                    else None,
                )
            if method == "settle" and args[0] not in budget._pending:
                del budget._owners[str(args[0])]
            tx.execute(
                "UPDATE budgets SET payload=? WHERE identity=?",
                (pack(_state(budget)), self.identity),
            )
            if method == "reserve" and failure is None and self.experiment is not None:
                request = kwargs.get("request")
                if request is None:
                    raise ValueError("durable attempt requires request")
                self.store.room(tx, "attempts", 4096)
                tx.execute(
                    "INSERT INTO attempts VALUES(?,?,?,'unknown',NULL)",
                    (self.experiment, result, pack(request)),
                )
                if witness is not None:
                    from .gepa_evidence import validate_shape

                    validate_shape(witness, request)
                    cast(Any, self.store).put(
                        tx,
                        "gepa_request_witness",
                        self.experiment + ":" + str(result),
                        witness,
                    )

            if method == "settle" and self.experiment is not None:
                ticket = args[0]
                receipt = kwargs.get("receipt")
                # On unknown/over-bound usage preserve both receipt and reservation.
                payload = pack(receipt) if receipt is not None else None
                tx.execute(
                    "UPDATE attempts SET outcome=?,receipt=? WHERE experiment=? AND ticket=?",
                    (
                        "settled" if ticket not in budget._pending else "unknown",
                        payload,
                        self.experiment,
                        ticket,
                    ),
                )
        if method == "reserve" and failure is None:
            self.checkpoint("after_budget_reservation")
        if failure:
            raise failure
        return result

    def reserve_gepa(self, maximum: Reservation, *, request: Any, witness: Any) -> Any:
        from .gepa_evidence import validate_shape

        validate_shape(witness, request)
        if self.experiment is None:
            raise ValueError("GEPA witness requires durable owner")
        return self._apply("reserve", maximum, request=request, gepa_witness=witness)

    def reserve(self, maximum: Reservation, *, request=None):
        return self._apply("reserve", maximum, request=request)

    def settle(
        self, ticket: int, *, tokens: Any, cost_microunits: Any, receipt: Any = None
    ) -> Any:
        _integer(ticket, positive=True)
        return self._apply(
            "settle",
            ticket,
            tokens=tokens,
            cost_microunits=cost_microunits,
            receipt=receipt,
        )

    def cancel(self) -> Any:
        return self._apply("cancel")

    def stop(self, reason):
        _token(reason)
        return self._apply("stop", reason)

    def check(self):
        return self._apply("check")

    def snapshot(self) -> Any:
        return self._apply("snapshot")

    def checked_snapshot(self):
        return self._apply("checked_snapshot")


def verify_evaluation_pass(
    tx: sqlite3.Connection,
    identity: str,
    report: Report,
    experiment: Experiment,
    dataset: Dataset,
    split: LockedSplit,
) -> None:
    """Reconstruct a passing result from the locked cases and settled rows."""
    failure = "invalid passing evaluation evidence"
    validate_dataset = cast(
        Callable[
            [Dataset, LockedSplit], tuple[tuple[Example, ...], tuple[Example, ...]]
        ],
        _validate_dataset,
    )
    train, heldout = validate_dataset(dataset, split)
    config = experiment.configuration
    attempts = report.attempts
    if (
        not heldout
        or (config.preparation and not train)
        or report.version != EVALUATION_VERSION
        or report.reason is not None
        or report.safety is not True
        or report.heldout_cases
        != tuple((e.output.output_id, e.output.revision) for e in heldout)
        or report.excluded_counts != tuple(sorted(dataset.excluded_counts.items()))
        or type(attempts) is not tuple
        or not 1 <= len(attempts) <= 4096
    ):
        raise ValueError(failure)
    rows = tx.execute(
        "SELECT request,outcome,receipt FROM attempts WHERE experiment=? ORDER BY ticket LIMIT 4097",
        (identity,),
    )
    position = tokens = cost = latency = 0
    scores = {"baseline": 0, "candidate": 0}
    for stage, example in evaluation_cases(config, train, heldout):
        for number in range(1, config.max_attempts + 1):
            if position >= len(attempts):
                raise ValueError(failure)
            attempt = attempts[position]
            request = pack(
                evaluation_request(experiment, split, stage, example, number)
            )
            if (
                type(attempt) is not Attempt
                or next(rows, None) != (request, "settled", pack(attempt))
                or pack(attempt.request) != request
                or attempt.safety is not True
                or type(attempt.tokens) is not int
                or not 0 <= attempt.tokens <= config.per_call.tokens
                or type(attempt.cost_microunits) is not int
                or not 0 <= attempt.cost_microunits <= config.per_call.cost_microunits
                or type(attempt.latency_ms) is not int
                or not 0 <= attempt.latency_ms < config.limits.time_ms
            ):
                raise ValueError(failure)
            position += 1
            tokens += attempt.tokens
            cost += attempt.cost_microunits
            latency += attempt.latency_ms
            if attempt.outcome == "completed":
                if type(attempt.content_sha256) is not str:
                    raise ValueError(failure)
                _hash(attempt.content_sha256)
                if stage in scores:
                    scores[stage] += int(attempt.content_sha256 == digest(example.gold))
                break
            if (
                attempt.outcome != "timeout"
                or attempt.content_sha256 is not None
                or number == config.max_attempts
            ):
                raise ValueError(failure)
    count = len(heldout)
    if (
        position != len(attempts)
        or next(rows, None) is not None
        or pack((report.baseline_score, report.candidate_score))
        != pack((scores["baseline"] / count, scores["candidate"] / count))
        or scores["candidate"] * 1_000_000 < config.min_score_ppm * count
        or (scores["candidate"] - scores["baseline"]) * 1_000_000
        < config.min_improvement_ppm * count
    ):
        raise ValueError(failure)
    if (
        type(report.usage) is not tuple
        or len(report.usage) != 6
        or any(
            type(item) is not tuple or len(item) != 2 or type(item[0]) is not str
            for item in report.usage
        )
    ):
        raise ValueError(failure)
    usage = dict(report.usage)
    if (
        usage.get("reason", "") is not None
        or type(usage.get("unresolved_calls")) is not int
        or usage["unresolved_calls"] != 0
    ):
        raise ValueError(failure)
    for field, minimum, maximum in (
        ("calls", position, config.limits.calls),
        ("tokens", tokens, config.limits.tokens),
        ("cost_microunits", cost, config.limits.cost_microunits),
        ("elapsed_ms", latency, config.limits.time_ms - 1),
    ):
        value = usage.get(field)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(failure)


def run(
    store: Store,
    api_version,
    identity,
    snapshot_sha256,
    experiment: Experiment,
    *,
    provider,
    budget_id,
    checkpoint=lambda _: None,
    retained_use: RetainedFeedbackUse | None = None,
    synthetic_fixture: bool = False,
):
    version(api_version)
    _token(identity)
    _token(budget_id)
    if type(experiment) is not Experiment:
        raise ValueError("offline adapters required")
    experiment.__post_init__()
    dataset, split = store.lineage(snapshot_sha256)
    if (experiment.dataset_sha256, experiment.split_sha256) != (
        split.dataset_sha256,
        split.sha256,
    ):
        raise ValueError("experiment lineage conflict")
    binding = pack((VERSION, snapshot_sha256, experiment, budget_id))
    check_use = use_guard(
        store,
        snapshot_sha256,
        purpose="evaluation",
        configuration_sha256=experiment.config_sha256,
        run_sha256=digest(cast(Callable[[Any], bytes], pack)((identity, binding))),
        retained_use=retained_use,
        synthetic_fixture=synthetic_fixture,
    )
    check_use()
    with store.transaction() as tx:
        check_use()
        row = tx.execute(
            "SELECT binding,state,report FROM experiments WHERE identity=?", (identity,)
        ).fetchone()
        if row:
            if row[0] != binding:
                raise ValueError("immutable experiment conflict")
            if row[1] == "complete":
                report = cast(Callable[[str, str], Any], store.get)("report", row[2])
                if (
                    type(report) is not Report
                    or digest(cast(Callable[[Any], bytes], pack)(report)) != row[2]
                    or (
                        report.version,
                        report.dataset_sha256,
                        report.split_sha256,
                        report.baseline_sha256,
                        report.candidate_sha256,
                        report.config_sha256,
                        report.heldout_cases,
                        report.excluded_counts,
                    )
                    != (
                        EVALUATION_VERSION,
                        split.dataset_sha256,
                        split.sha256,
                        experiment.baseline_sha256,
                        experiment.candidate_sha256,
                        experiment.config_sha256,
                        tuple(
                            (e.output.output_id, e.output.revision)
                            for e in dataset.examples
                            if split.partition(e.source.family_id) == "heldout"
                        ),
                        tuple(sorted(dataset.excluded_counts.items())),
                    )
                    or report.status not in {"passed", "blocked"}
                    or (report.status == "passed" and report.reason is not None)
                    or (
                        report.status == "blocked"
                        and (type(report.reason) is not str or not report.reason)
                    )
                ):
                    raise ValueError("invalid evaluation result association")
                check_use()
                if report.status == "passed":
                    verify_evaluation_pass(
                        tx, identity, report, experiment, dataset, split
                    )
                return report
            raise Halt("interrupted_or_owned_experiment")
        # Recheck after use callbacks, before any ledger or claim mutation.
        validate_provider(experiment, provider)
        if provider.calls:
            raise ValueError("new evaluation requires pristine provider cursor")
        # New ledger and claim commit together; existing ledgers never reset.
        budget = SQLiteBudget(
            store,
            budget_id,
            experiment.configuration.limits,
            clock_ms=provider.clock,
            experiment=identity,
            checkpoint=checkpoint,
        )
        store.room(tx, "experiments")
        store.put(tx, "experiment", identity, experiment)
        tx.execute(
            "INSERT INTO experiments VALUES(?,?,'running',NULL)", (identity, binding)
        )
    checkpoint("after_experiment_commit")
    report = evaluate(
        dataset,
        split,
        experiment,
        provider=provider,
        budget=budget,
        check_use=check_use,
        initial_provider_calls=0,
    )
    with store.transaction() as tx:
        report_sha = digest(pack(report))
        store.put(tx, "report", report_sha, report)
        tx.execute(
            "UPDATE experiments SET state='complete',report=? WHERE identity=? AND state='running'",
            (report_sha, identity),
        )
        checkpoint("before_report_commit")
        check_use()
        if report.status == "passed":
            verify_evaluation_pass(tx, identity, report, experiment, dataset, split)
    checkpoint("after_report_commit")
    check_use()
    return report
