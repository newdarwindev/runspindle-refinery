"""Actual BootstrapFewShot failure qualification; isolated lane only."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "cancel": (0, 1, "cancelled"),
    "exhaustion": (1, 0, "calls_exhausted"),
    "unknown_tokens": (1, 1, "unknown_or_invalid_usage"),
    "unknown_cost": (1, 1, "unknown_or_invalid_usage"),
    "provider_failure": (1, 1, "provider_unknown"),
}


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def validate(receipt: dict[str, Any]) -> None:
    calls, unresolved, reason = CASES[receipt["case"]]
    require(receipt["compile_entries"] == 1, "real compilation not observed")
    require(receipt["status"] == "blocked", "adversarial compilation succeeded")
    require(
        receipt["candidate"] is None and receipt["candidate_sha256"] is None,
        "blocked candidate escaped",
    )
    require(receipt["reason"] in {reason, "optimizer_failure"}, "unexpected failure")
    require(receipt["new_dispatches"] == calls, "unexpected provider dispatch")
    require(receipt["ledger"]["calls"] == 1, "reservation lost")
    require(
        receipt["ledger"]["unresolved_calls"] == unresolved, "unresolved charge lost"
    )
    require(receipt["ledger"]["reason"] == reason, "durable stop reason changed")
    require(receipt["retry_reason"] == reason, "retry was not denied")
    require(
        receipt["attempt_outcomes"] == ["unknown" if unresolved else "settled"],
        "durable attempt changed",
    )
    require(receipt["train_only"] is True, "heldout dispatch")
    require(receipt["network_attempts"] == 0, "network attempted")
    if unresolved:
        require(
            receipt["ledger"]["tokens"] is None
            and receipt["ledger"]["cost_microunits"] is None,
            "unknown usage refunded",
        )
    else:
        require(
            receipt["ledger"]["tokens"] == receipt["ledger"]["cost_microunits"] == 1,
            "settled usage changed",
        )


def diagnostic(receipt: dict[str, Any]) -> dict[str, Any]:
    """Bounded synthetic fields only: never content, requests, paths or exceptions."""

    def token(value: Any) -> str:
        if (
            type(value) is str
            and 0 < len(value) <= 96
            and all(
                c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
                for c in value
            )
        ):
            return value
        return "redacted"

    def number(value: Any) -> int | str | None:
        if value is None:
            return None
        return value if type(value) is int and 0 <= value <= 2**64 - 1 else "redacted"

    ledger = receipt.get("ledger")
    if type(ledger) is not dict:
        ledger = {}
    return {
        "diagnostic": "failed_adversarial_oracle",
        "case": receipt.get("case") if receipt.get("case") in CASES else "redacted",
        "status": token(receipt.get("status")),
        "reason": token(receipt.get("reason")),
        "retry_reason": token(receipt.get("retry_reason")),
        "compile_entries": number(receipt.get("compile_entries")),
        "new_dispatches": number(receipt.get("new_dispatches")),
        "network_attempts": number(receipt.get("network_attempts")),
        "candidate_present": receipt.get("candidate") is not None,
        "candidate_digest_present": receipt.get("candidate_sha256") is not None,
        "ledger": {
            **{
                key: number(ledger.get(key))
                for key in (
                    "calls",
                    "tokens",
                    "cost_microunits",
                    "unresolved_calls",
                    "elapsed_ms",
                )
            },
            "reason": token(ledger.get("reason")),
        },
    }


def validate_observed(receipt: dict[str, Any]) -> None:
    try:
        validate(receipt)
    except ValueError:
        print(json.dumps(diagnostic(receipt), sort_keys=True), file=sys.stderr)
        raise


def run() -> dict[str, Any]:
    require(sys.version_info[:3] == (3, 14, 0), "unqualified Python")
    require(
        sys.platform == "linux" and os.uname().machine == "aarch64",
        "unqualified target",
    )
    sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")]
    forbidden: list[str] = []

    def deny(*args: Any, **kwargs: Any) -> Any:
        forbidden.append("network")
        raise ValueError("network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
    ):
        verifier: Any = importlib.import_module("offline_dspy")
        declared, _ = verifier.reviewed_closure()
        spec = importlib.util.find_spec("dspy")
        if spec is None or spec.origin is None:
            raise ValueError("prepared DSPy missing")
        require(
            verifier.inventory(Path(spec.origin).resolve().parents[1]) == declared,
            "prepared bytes changed",
        )
        opt: Any = importlib.import_module("refinery.dspy_optimizer")
        ev: Any = importlib.import_module("refinery.evaluation")
        durable: Any = importlib.import_module("refinery.durable_evaluation")
        storage: Any = importlib.import_module("refinery.storage")
        fixture: Any = importlib.import_module("test_durable")
        dspy = opt.framework()
        receipts = []
        with tempfile.TemporaryDirectory(prefix="dspy-adversarial-") as temporary:
            for name in CASES:
                with storage.Store(Path(temporary) / (name + ".sqlite")) as store:
                    snapshot, _, _ = fixture.setup(store)
                    dataset, split = store.lineage(snapshot)
                    train, _ = opt.validate_dataset(dataset, split)
                    completion = ev.Completion(
                        b"wrong-first-round",
                        None if name == "unknown_tokens" else 1,
                        None if name == "unknown_cost" else 1,
                        True,
                        1,
                    )
                    provider = ev.ScriptedProvider(
                        () if name == "provider_failure" else (completion,),
                        clock=ev.VirtualClock(),
                    )
                    budget = durable.SQLiteBudget(
                        store,
                        name,
                        ev.Limits(1 if name == "exhaustion" else 100, 1000, 1000, 1000),
                        clock_ms=provider.clock,
                        experiment=name,
                    )
                    if name == "cancel":
                        budget.checkpoint = lambda _: budget.cancel()
                    observed: list[Any] = []
                    entered: list[str] = []
                    original_complete = ev.ScriptedProvider.complete
                    original_compile = dspy.BootstrapFewShot.compile

                    def observe(instance: Any, request: Any) -> Any:
                        observed.append(request)
                        return original_complete(instance, request)

                    def compile_actual(instance: Any, *args: Any, **kwargs: Any) -> Any:
                        entered.append("compile")
                        return original_compile(instance, *args, **kwargs)

                    with (
                        patch.object(ev.ScriptedProvider, "complete", observe),
                        patch.object(dspy.BootstrapFewShot, "compile", compile_actual),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        result = opt.optimize(
                            dataset,
                            split,
                            provider=provider,
                            budget=budget,
                            per_call=ev.Reservation(10, 10),
                            settings=opt.Settings(1, 2),
                            check_use=lambda: None,
                        )
                        before = store.db.execute(
                            "SELECT payload FROM budgets WHERE identity=?", (name,)
                        ).fetchone()[0]
                        try:
                            budget.reserve(
                                ev.Reservation(10, 10),
                                request=observed[0] if observed else None,
                            )
                        except ev.Halt as error:
                            retry_reason = str(error)
                        else:
                            raise ValueError("stopped budget admitted retry")
                        require(
                            before
                            == store.db.execute(
                                "SELECT payload FROM budgets WHERE identity=?", (name,)
                            ).fetchone()[0],
                            "retry changed ledger",
                        )
                    receipt = {
                        "case": name,
                        "compile_entries": len(entered),
                        "status": result.status,
                        "reason": result.reason,
                        "candidate": result.candidate,
                        "candidate_sha256": result.candidate_sha256,
                        "new_dispatches": len(observed),
                        "ledger": budget.snapshot(),
                        "retry_reason": retry_reason,
                        "attempt_outcomes": [
                            r[0]
                            for r in store.db.execute(
                                "SELECT outcome FROM attempts ORDER BY ticket"
                            )
                        ],
                        "train_only": all(
                            r.stage == "optimization"
                            and r.output_id in {e.output.output_id for e in train}
                            for r in observed
                        ),
                        "network_attempts": len(forbidden),
                    }
                    validate_observed(receipt)
                    receipt["ledger_sha256"] = opt.digest(before)
                    receipt["attempts_sha256"] = opt.digest(
                        storage.pack(
                            tuple(
                                store.db.execute(
                                    "SELECT ticket,request,outcome,receipt FROM attempts ORDER BY ticket"
                                )
                            )
                        )
                    )
                    receipts.append(receipt)
        require(not forbidden, "network attempted")
        paths = (
            "tools/test_dspy_adversarial.py",
            "tools/offline_dspy.py",
            "refinery/dspy_optimizer.py",
            "refinery/durable_evaluation.py",
            "refinery/evaluation.py",
            "refinery/storage.py",
            "tests/test_durable.py",
            "tests/test_lineage.py",
            "offline/dependency-adoption.json",
            "offline/dependencies.json",
        )
        return {
            "status": "passed",
            "evidence_class": "actual_framework_adversarial_component",
            "cases": receipts,
            "source_sha256": {p: opt.digest((ROOT / p).read_bytes()) for p in paths},
            "live_provider": "not_run",
            "concurrency": "not_qualified",
            "promotion": "not_authorized",
        }


if __name__ == "__main__":
    first, second = run(), run()
    require(first == second, "adversarial repeat mismatch")
    print(json.dumps({**first, "repeat_runs": 2}, sort_keys=True))
