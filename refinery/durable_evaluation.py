"""Durable wrappers around the existing evaluation and budget transition library."""

from __future__ import annotations

import threading
from .evaluation import (
    Budget,
    Experiment,
    Halt,
    Limits,
    Reservation,
    evaluate,
    _integer,
)
from .lineage import digest, _token
from .storage import Store, VERSION, pack, unpack, version


def _state(budget):
    return {
        "limits": budget.limits,
        "start": budget._start,
        "last": budget._last,
        "pending": {str(k): v for k, v in budget._pending.items()},
        "next": budget._next,
        "calls": budget.calls,
        "tokens": budget.tokens,
        "cost": budget.cost_microunits,
        "reason": budget.reason,
        "time_unknown": budget._time_unknown,
    }


def _restore(value, clock):
    budget = Budget.__new__(Budget)
    budget.limits, budget.clock_ms = value["limits"], clock
    budget._start, budget._last = value["start"], value["last"]
    budget._pending = {int(k): v for k, v in value["pending"].items()}
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
        self.store, self.identity, self.limits = store, identity, limits
        self.clock_ms, self.experiment, self.checkpoint = (
            clock_ms,
            experiment,
            checkpoint,
        )
        with store.transaction() as tx:
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

    def _apply(self, method, *args, **kwargs):
        failure = None
        result = None
        with self.store.transaction() as tx:
            row = tx.execute(
                "SELECT payload FROM budgets WHERE identity=?", (self.identity,)
            ).fetchone()
            budget = _restore(unpack(row[0]), self.clock_ms)
            try:
                if method == "reserve" and budget.calls >= 4096:
                    budget.stop("attempt_limit")
                    raise Halt("attempt_limit")
                result = getattr(budget, method)(*args, **kwargs)
            except Halt as exc:
                failure = exc
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

    def reserve(self, maximum: Reservation, *, request=None):
        return self._apply("reserve", maximum, request=request)

    def settle(self, ticket, *, tokens, cost_microunits, receipt=None):
        _integer(ticket, positive=True)
        return self._apply(
            "settle",
            ticket,
            tokens=tokens,
            cost_microunits=cost_microunits,
            receipt=receipt,
        )

    def cancel(self):
        return self._apply("cancel")

    def stop(self, reason):
        _token(reason)
        return self._apply("stop", reason)

    def check(self):
        return self._apply("check")

    def snapshot(self):
        return self._apply("snapshot")

    def checked_snapshot(self):
        return self._apply("checked_snapshot")


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
):
    version(api_version)
    _token(identity)
    _token(budget_id)
    experiment.__post_init__()
    dataset, split = store.lineage(snapshot_sha256)
    if (experiment.dataset_sha256, experiment.split_sha256) != (
        split.dataset_sha256,
        split.sha256,
    ):
        raise ValueError("experiment lineage conflict")
    binding = pack((VERSION, snapshot_sha256, experiment, budget_id))
    # Construct/open ledger first, preserving its original start and reservations.
    budget = SQLiteBudget(
        store,
        budget_id,
        experiment.configuration.limits,
        clock_ms=provider.clock,
        experiment=identity,
        checkpoint=checkpoint,
    )
    with store.transaction() as tx:
        row = tx.execute(
            "SELECT binding,state,report FROM experiments WHERE identity=?", (identity,)
        ).fetchone()
        if row:
            if row[0] != binding:
                raise ValueError("immutable experiment conflict")
            if row[1] == "complete":
                return store.get("report", row[2])
            raise Halt("interrupted_or_owned_experiment")
        store.room(tx, "experiments")
        store.put(tx, "experiment", identity, experiment)
        tx.execute(
            "INSERT INTO experiments VALUES(?,?,'running',NULL)", (identity, binding)
        )
    checkpoint("after_experiment_commit")
    report = evaluate(dataset, split, experiment, provider=provider, budget=budget)
    with store.transaction() as tx:
        report_sha = digest(pack(report))
        store.put(tx, "report", report_sha, report)
        tx.execute(
            "UPDATE experiments SET state='complete',report=? WHERE identity=? AND state='running'",
            (report_sha, identity),
        )
        checkpoint("before_report_commit")
    checkpoint("after_report_commit")
    return report
