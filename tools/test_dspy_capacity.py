"""Actual DSPy controlled-overlap shared-capacity qualification, not a claim race."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
shared: Any = importlib.import_module("tools.test_dspy_ownership")
BUDGET = "capacity-shared"
VARIANTS = ("known", "unknown_tokens")


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def sources() -> dict[str, str]:
    return {
        **shared.sources(),
        "tools/test_dspy_capacity.py": shared.digest(Path(__file__).read_bytes()),
    }


def inspect_state(directory: Path) -> dict[str, Any]:
    storage: Any = importlib.import_module("refinery.storage")
    with contextlib.closing(sqlite3.connect(directory / "store.sqlite")) as db:
        rows = db.execute("SELECT identity,payload FROM budgets").fetchall()
        require(len(rows) == 1 and rows[0][0] == BUDGET, "capacity ledger changed")
        payload = rows[0][1]
        state = storage.unpack(payload)
        attempts = db.execute(
            "SELECT experiment,ticket,request,outcome,receipt FROM attempts ORDER BY experiment,ticket"
        ).fetchall()
        encoded = [
            [value.hex() if type(value) is bytes else value for value in row]
            for row in attempts
        ]
        receipts = [
            storage.unpack(row[4]) if row[4] is not None else None for row in attempts
        ]
        return {
            "ledger_sha256": shared.digest(payload),
            "ledger_without_reason_sha256": shared.digest(
                storage.pack({k: v for k, v in state.items() if k != "reason"})
            ),
            "attempts_sha256": shared.digest(
                json.dumps(encoded, sort_keys=True).encode()
            ),
            "calls": state["calls"],
            "pending": len(state["pending"]),
            "reason": state["reason"],
            "tokens": state["tokens"],
            "cost": state["cost"],
            "last": state["last"],
            "limits": vars(state["limits"]),
            "experiments": [row[0] for row in attempts],
            "outcomes": [row[3] for row in attempts],
            "receipt_tokens": [
                None if receipt is None else receipt.tokens for receipt in receipts
            ],
            "receipt_cost": [
                None if receipt is None else receipt.cost_microunits
                for receipt in receipts
            ],
            "receipt_present": [receipt is not None for receipt in receipts],
        }


def pending(value: dict[str, Any], reason: str | None) -> None:
    require(value["calls"] == value["pending"] == 1, "pending reservation lost")
    require(
        value["tokens"] == value["cost"] == value["last"] == 0,
        "pending capacity changed",
    )
    require(value["reason"] == reason, "shared terminal cause changed")
    require(
        value["limits"]
        == {"calls": 1, "tokens": 1000, "cost_microunits": 1000, "time_ms": 1000},
        "capacity limit changed",
    )
    require(
        value["experiments"] == ["first"]
        and value["outcomes"] == ["unknown"]
        and value["receipt_present"] == [False],
        "second reserved capacity or first receipt changed",
    )


def validate_worker(value: dict[str, Any], role: str, variant: str) -> None:
    require(
        value["role"] == role and value["variant"] == variant,
        "capacity worker mismatch",
    )
    require(value["compile_entries"] == 1, "actual framework entry missing")
    require(
        value["new_dispatches"] == (1 if role == "first" else 0),
        "capacity exceeded provider bound",
    )
    require(
        value["status"] == "blocked"
        and value["candidate_sha256"] is None
        and value["candidate_present"] is False,
        "capacity candidate escaped",
    )
    require(
        value["result_reason"] in {"calls_exhausted", "optimizer_failure"}
        and value["retry_reason"] == "calls_exhausted",
        "unexpected capacity stop",
    )
    require(
        value["network_attempts"] == 0 and value["train_only"] is True,
        "forbidden capacity effect",
    )
    require(
        value["attempt_count"] == (1 if role == "first" else 0),
        "optimizer attempt attribution changed",
    )
    require(
        value["usage"]["calls"] == 1 and value["usage"]["reason"] == "calls_exhausted",
        "budget reset or terminal cause changed",
    )
    unresolved = role == "second" or variant == "unknown_tokens"
    require(
        value["usage"]["unresolved_calls"] == int(unresolved),
        "unknown reservation lost",
    )
    require(
        value["usage"]["tokens"] == (None if unresolved else 1)
        and value["usage"]["cost_microunits"] == (None if unresolved else 1),
        "usage refunded or fabricated",
    )


def validate_final(value: dict[str, Any], variant: str) -> None:
    require(
        value["calls"] == 1
        and value["reason"] == "calls_exhausted"
        and value["last"] == 0,
        "final shared capacity changed",
    )
    require(
        value["limits"]
        == {"calls": 1, "tokens": 1000, "cost_microunits": 1000, "time_ms": 1000},
        "final limits changed",
    )
    require(
        value["experiments"] == ["first"] and value["receipt_present"] == [True],
        "settlement attribution missing",
    )
    unknown = variant == "unknown_tokens"
    require(value["pending"] == int(unknown), "final unresolved charge lost")
    require(
        value["tokens"] == value["cost"] == (0 if unknown else 1),
        "final accounted usage changed",
    )
    require(
        value["outcomes"] == ["unknown" if unknown else "settled"],
        "settlement outcome changed",
    )
    require(
        value["receipt_tokens"] == [None if unknown else 1]
        and value["receipt_cost"] == [1],
        "receipt usage changed",
    )


def worker(directory: Path, role: str, variant: str) -> None:
    require(
        variant in VARIANTS and role in {"first", "second"}, "unknown capacity case"
    )
    with shared.isolated() as forbidden:
        ev: Any = importlib.import_module("refinery.evaluation")
        opt: Any = importlib.import_module("refinery.dspy_optimizer")
        durable: Any = importlib.import_module("refinery.durable_evaluation")
        storage: Any = importlib.import_module("refinery.storage")
        snapshot = shared.read_json(directory / "snapshot.json")
        with storage.Store(directory / "store.sqlite") as store:
            dataset, split = store.lineage(snapshot)
            train, _ = opt.validate_dataset(dataset, split)
            # Both controllers use the exact same script/configuration. No latency
            # means independent virtual clocks do not invent cross-process regression.
            provider = ev.ScriptedProvider(
                (
                    ev.Completion(
                        b"wrong-first-round",
                        None if variant == "unknown_tokens" else 1,
                        1,
                        True,
                        0,
                    ),
                ),
                clock=ev.VirtualClock(),
            )
            budget = durable.SQLiteBudget(
                store,
                BUDGET,
                ev.Limits(1, 1000, 1000, 1000),
                clock_ms=provider.clock,
                experiment=role,
            )
            dspy = opt.framework()
            observed: list[Any] = []
            entered: list[str] = []
            complete = ev.ScriptedProvider.complete
            compile_original = dspy.BootstrapFewShot.compile

            def observe(instance: Any, request: Any) -> Any:
                observed.append(request)
                reply = complete(instance, request)
                require(
                    role == "first" and len(observed) == 1,
                    "second provider effect occurred",
                )
                require(len(entered) == 1, "provider outside framework")
                state = inspect_state(directory)
                pending(state, None)
                shared.write_json(
                    directory / "held.json",
                    {"state": state, "pid": os.getpid(), "source_sha256": sources()},
                )
                shared.wait_for(lambda: (directory / "release").exists(), seconds=45)
                return reply

            def actual_compile(instance: Any, *args: Any, **kwargs: Any) -> Any:
                entered.append("compile")
                return compile_original(instance, *args, **kwargs)

            with (
                patch.object(ev.ScriptedProvider, "complete", observe),
                patch.object(dspy.BootstrapFewShot, "compile", actual_compile),
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
                before = inspect_state(directory)
                try:
                    budget.reserve(
                        ev.Reservation(10, 10),
                        request=observed[0] if observed else None,
                    )
                except ev.Halt as error:
                    retry_reason = str(error)
                else:
                    raise ValueError("capacity retry admitted")
                require(
                    inspect_state(directory) == before,
                    "capacity retry changed durable evidence",
                )
            receipt = {
                "role": role,
                "variant": variant,
                "pid": os.getpid(),
                "compile_entries": len(entered),
                "new_dispatches": len(observed),
                "snapshot": snapshot,
                "configuration_sha256": result.configuration_sha256,
                "source_sha256": sources(),
                "status": result.status,
                "candidate_sha256": result.candidate_sha256,
                "candidate_present": result.candidate is not None,
                "result_reason": result.reason,
                "retry_reason": retry_reason,
                "attempt_count": len(result.attempts),
                "usage": budget.snapshot(),
                "state": inspect_state(directory),
                "network_attempts": len(forbidden),
                "train_only": all(
                    r.stage == "optimization"
                    and r.output_id == train[0].output.output_id
                    for r in observed
                ),
            }
            validate_worker(receipt, role, variant)
            shared.write_json(directory / (role + ".json"), receipt)


def orchestrate(directory: Path, variant: str) -> dict[str, Any]:
    fresh: Any = importlib.import_module("tools.dspy_fresh_process")
    children: list[Any] = []
    with contextlib.ExitStack() as captures:

        def launch(role: str) -> Any:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    "--worker",
                    role,
                    "--variant",
                    variant,
                    "--directory",
                    str(directory),
                ],
                cwd=ROOT,
                env=fresh.child_environment(directory / role),
                stdout=captures.enter_context(tempfile.TemporaryFile()),
                stderr=captures.enter_context(tempfile.TemporaryFile()),
            )
            children.append(process)
            return process

        try:
            first = launch("first")

            def held() -> bool:
                require(
                    first.poll() is None, "first capacity worker exited before overlap"
                )
                return (directory / "held.json").exists()

            shared.wait_for(held, seconds=25)
            held_receipt = shared.read_json(directory / "held.json")
            require(
                held_receipt["pid"] == first.pid
                and held_receipt["source_sha256"] == sources(),
                "held worker binding changed",
            )
            pending(held_receipt["state"], None)
            require(
                inspect_state(directory) == held_receipt["state"],
                "held capacity changed",
            )
            second = launch("second")
            require(second.wait(timeout=25) == 0, "second capacity worker failed")
            denied = shared.read_json(directory / "second.json")
            validate_worker(denied, "second", variant)
            require(
                denied["pid"] == second.pid and denied["source_sha256"] == sources(),
                "second worker binding changed",
            )
            state = inspect_state(directory)
            pending(state, "calls_exhausted")
            require(state == denied["state"], "pending state changed before release")
            for key in ("ledger_without_reason_sha256", "attempts_sha256"):
                require(
                    state[key] == held_receipt["state"][key],
                    "second reset pending capacity",
                )
            (directory / "release").touch()
            require(first.wait(timeout=25) == 0, "first capacity worker failed")
            completed = shared.read_json(directory / "first.json")
            validate_worker(completed, "first", variant)
            require(
                completed["pid"] == first.pid
                and first.pid != second.pid
                and os.getpid() not in {first.pid, second.pid},
                "capacity workers not fresh",
            )
            for key in ("snapshot", "configuration_sha256", "source_sha256"):
                require(
                    completed[key] == denied[key], "capacity controller binding changed"
                )
            require(
                completed["source_sha256"] == sources(), "final capacity source changed"
            )
            final = inspect_state(directory)
            validate_final(final, variant)
            require(final == completed["state"], "final state changed")
            return {
                "variant": variant,
                "held_state": held_receipt["state"],
                "workers": [
                    {key: value for key, value in receipt.items() if key != "pid"}
                    for receipt in (completed, denied)
                ],
                "final_state": final,
            }
        finally:
            errors: list[Exception] = []
            for child in children:
                try:
                    if child.poll() is None:
                        child.kill()
                except Exception as error:
                    errors.append(error)
                try:
                    child.wait(timeout=5)
                except Exception as error:
                    errors.append(error)
            if errors:
                raise ValueError("capacity child cleanup incomplete") from errors[0]


def run() -> dict[str, Any]:
    with shared.isolated():
        storage: Any = importlib.import_module("refinery.storage")
        fixture: Any = importlib.import_module("test_durable")
        results = []
        with tempfile.TemporaryDirectory(prefix="dspy-capacity-") as temporary:
            for variant in VARIANTS:
                directory = Path(temporary) / variant
                directory.mkdir()
                with storage.Store(directory / "store.sqlite") as store:
                    snapshot, _, _ = fixture.setup(store)
                shared.write_json(directory / "snapshot.json", snapshot)
                results.append(orchestrate(directory, variant))
        return {
            "status": "passed",
            "evidence_class": "actual_dspy_controlled_overlap_capacity",
            "cases": results,
            "simultaneous_acquisition": "not_qualified",
            "live_provider": "not_run",
            "reflection": "not_qualified",
            "promotion": "not_authorized",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("first", "second"))
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.worker:
        require(
            args.variant is not None and args.directory is not None,
            "capacity worker arguments missing",
        )
        worker(args.directory, args.worker, args.variant)
    else:
        print(json.dumps(run(), sort_keys=True))
