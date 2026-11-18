"""Actual two-process DSPy ownership qualification; isolated offline lane only."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "actual-process-ownership"
BUDGET = "ownership-shared"


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    # One writer per marker; rename prevents observers reading partial JSON.
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, sort_keys=True))
    temporary.replace(path)


def read_json(path: Path) -> Any:
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    require(len(data) <= 1024 * 1024, "receipt too large")
    return json.loads(data)


def wait_for(test: Callable[[], bool], seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not test():
        require(time.monotonic() < deadline, "ownership barrier timeout")
        time.sleep(0.01)


def persisted(directory: Path) -> dict[str, str]:
    with sqlite3.connect(directory / "store.sqlite") as db:
        row = db.execute(
            "SELECT payload FROM budgets WHERE identity=?", (BUDGET,)
        ).fetchone()
        require(row is not None, "shared ledger absent")
        attempts = db.execute(
            "SELECT experiment,ticket,request,outcome,receipt FROM attempts ORDER BY experiment,ticket"
        ).fetchall()
        encoded = [
            [value.hex() if type(value) is bytes else value for value in row]
            for row in attempts
        ]
        return {
            "ledger_sha256": digest(row[0]),
            "attempts_sha256": digest(json.dumps(encoded, sort_keys=True).encode()),
        }


def sources() -> dict[str, str]:
    fresh: Any = importlib.import_module("tools.dspy_fresh_process")
    return {
        **fresh.source_hashes(),
        "tools/test_dspy_ownership.py": digest(Path(__file__).read_bytes()),
    }


@contextlib.contextmanager
def isolated() -> Iterator[list[str]]:
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
            raise ValueError("prepared DSPy absent")
        require(
            verifier.inventory(Path(spec.origin).resolve().parents[1]) == declared,
            "prepared closure changed",
        )
        yield forbidden
        require(not forbidden, "network attempted")


def worker(directory: Path, role: str) -> dict[str, Any]:
    with isolated() as forbidden:
        ev: Any = importlib.import_module("refinery.evaluation")
        opt: Any = importlib.import_module("refinery.dspy_optimizer")
        storage: Any = importlib.import_module("refinery.storage")
        lifecycle: Any = importlib.import_module("refinery.durable_dspy")
        snapshot = read_json(directory / "snapshot.json")
        with storage.Store(directory / "store.sqlite") as store:
            dataset, split = store.lineage(snapshot)
            train, _ = opt.validate_dataset(dataset, split)
            provider = ev.ScriptedProvider(
                (
                    ev.Completion(b"wrong-first-round", 1, 1, True, 1),
                    ev.Completion(train[0].gold, 1, 1, True, 1),
                ),
                clock=ev.VirtualClock(),
            )
            observed: list[Any] = []
            entered: list[str] = []
            held = False
            initial_rejection = None
            retry_rejection = None

            def checkpoint(point: str) -> None:
                nonlocal held
                if point == "after_budget_reservation" and not held:
                    held = True
                    require(
                        len(entered) == 1 and not observed,
                        "reservation outside actual compilation",
                    )
                    state = session.budget().snapshot()
                    require(
                        state["calls"] == state["unresolved_calls"] == 1,
                        "first reservation not retained",
                    )
                    write_json(
                        directory / "reserved.json",
                        {**persisted(directory), "owner": role},
                    )
                    wait_for(lambda: (directory / "release").exists())

            session = lifecycle.Session(
                store,
                storage.VERSION,
                IDENTITY,
                snapshot,
                provider=provider,
                limits=ev.Limits(100, 1000, 1000, 1000),
                per_call=ev.Reservation(10, 10),
                budget_id=BUDGET,
                settings=opt.Settings(1, 2),
                synthetic_fixture=True,
                checkpoint=checkpoint,
            )
            original_complete = ev.ScriptedProvider.complete

            def observe(instance: Any, request: Any) -> Any:
                observed.append(request)
                return original_complete(instance, request)

            if role == "recover":
                state = storage.unpack(
                    store.db.execute(
                        "SELECT payload FROM budgets WHERE identity=?", (BUDGET,)
                    ).fetchone()[0]
                )
                provider.clock.now = state["last"]
                before = persisted(directory)
                with (
                    patch.object(ev.ScriptedProvider, "complete", observe),
                    patch.object(
                        opt,
                        "framework",
                        side_effect=ValueError("recovery framework forbidden"),
                    ) as framework,
                ):
                    result = session.compile()
                require(
                    framework.call_count == 0 and "dspy" not in sys.modules,
                    "recovery imported framework",
                )
                require(
                    persisted(directory) == before, "recovery changed durable state"
                )
                outcome = "recovered"
            else:
                dspy = opt.framework()
                original_compile = dspy.BootstrapFewShot.compile

                def compile_actual(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    entered.append("compile")
                    return original_compile(instance, *args, **kwargs)

                write_json(directory / (role + ".ready"), {"pid": os.getpid()})
                wait_for(lambda: (directory / "start").exists())
                with (
                    patch.object(ev.ScriptedProvider, "complete", observe),
                    patch.object(dspy.BootstrapFewShot, "compile", compile_actual),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    try:
                        result = session.compile()
                    except ev.Halt as error:
                        initial_rejection = str(error)
                        require(
                            str(error)
                            in {
                                "interrupted_or_owned_dspy_phase",
                                "lifecycle_claim_changed",
                            },
                            "unexpected contender failure",
                        )
                        require(not entered and not observed, "loser executed effects")
                        wait_for(lambda: (directory / "reserved.json").exists())
                        reserved = read_json(directory / "reserved.json")
                        before = persisted(directory)
                        require(
                            all(before[k] == reserved[k] for k in before),
                            "loser saw reset ledger",
                        )
                        # Retry while winner holds a real unresolved reservation.
                        try:
                            session.compile()
                        except ev.Halt as retry:
                            retry_rejection = str(retry)
                            require(
                                str(retry) == "interrupted_or_owned_dspy_phase",
                                "retry reason changed",
                            )
                        else:
                            raise ValueError("loser admitted retry")
                        require(
                            before == persisted(directory), "loser reset durable state"
                        )
                        result = None
                        outcome = "denied"
                    else:
                        outcome = "compiled"
            state = session.budget().snapshot()
            experiment = session.row(store.db)
            receipt = {
                "outcome": outcome,
                "worker_role": role,
                "initial_rejection": initial_rejection,
                "retry_rejection": retry_rejection,
                "compile_entries": len(entered),
                "new_dispatches": len(observed),
                "candidate_sha256": None if result is None else result.candidate_sha256,
                "compiled_status": None if result is None else result.status,
                "binding_sha256": session.binding_sha256,
                "snapshot": snapshot,
                "configuration_sha256": session.config,
                "ledger": state,
                "stored_state": experiment[1],
                "stored_report": experiment[2],
                "compiled_objects": store.db.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind='dspy_compiled'"
                ).fetchone()[0],
                "experiment_count": store.db.execute(
                    "SELECT COUNT(*) FROM experiments"
                ).fetchone()[0],
                "attempt_outcomes": [
                    row[0]
                    for row in store.db.execute(
                        "SELECT outcome FROM attempts ORDER BY ticket"
                    )
                ],
                "train_only": all(
                    r.stage == "optimization"
                    and r.output_id == train[0].output.output_id
                    for r in observed
                ),
                "network_attempts": len(forbidden),
                "source_sha256": sources(),
                **persisted(directory),
                "pid": os.getpid(),
            }
            write_json(directory / (role + ".json"), receipt)
            if outcome == "denied":
                write_json(directory / "loser.json", receipt)
            return receipt


def validate_case(value: dict[str, Any], outcome: str) -> None:
    require(value["outcome"] == outcome, "ownership outcome mismatch")
    require(
        value["network_attempts"] == 0 and value["train_only"] is True,
        "forbidden effect",
    )
    expected = {
        "denied": (0, 0, 1, 1),
        "compiled": (1, 2, 2, 0),
        "recovered": (0, 0, 2, 0),
    }[outcome]
    require(
        (
            value["compile_entries"],
            value["new_dispatches"],
            value["ledger"]["calls"],
            value["ledger"]["unresolved_calls"],
        )
        == expected,
        "ownership effects or charges changed",
    )
    require(value["ledger"]["reason"] is None, "ownership stopped shared ledger")
    require(value["experiment_count"] == 1, "multiple lifecycle claims")
    require(
        value["compiled_objects"] == (0 if outcome == "denied" else 1),
        "compiled object count changed",
    )
    require(
        value["stored_state"] == ("compiling" if outcome == "denied" else "compiled"),
        "stored lifecycle state changed",
    )
    require(
        value["attempt_outcomes"]
        == (["unknown"] if outcome == "denied" else ["settled", "settled"]),
        "stored attempts changed",
    )
    if outcome == "denied":
        require(
            value["initial_rejection"]
            in {"interrupted_or_owned_dspy_phase", "lifecycle_claim_changed"},
            "unexpected initial ownership rejection",
        )
        require(
            value["retry_rejection"] == "interrupted_or_owned_dspy_phase",
            "unexpected pending ownership rejection",
        )
        require(value["stored_report"] is None, "loser committed report")
        require(
            value["candidate_sha256"] is None and value["compiled_status"] is None,
            "loser candidate escaped",
        )
        require(
            value["ledger"]["tokens"] is None
            and value["ledger"]["cost_microunits"] is None,
            "pending charge lost",
        )
    else:
        candidate = value["candidate_sha256"]
        require(
            type(candidate) is str
            and len(candidate) == 64
            and all(c in "0123456789abcdef" for c in candidate),
            "candidate digest absent",
        )
        require(value["compiled_status"] == "compiled", "actual compilation failed")
        require(
            value["ledger"]["tokens"] == value["ledger"]["cost_microunits"] == 2,
            "settled charge changed",
        )


def validate_results(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    require(len(values) == 3, "ownership phase missing")
    loser, winner, recovered = values
    for value, expected in zip(values, ("denied", "compiled", "recovered")):
        validate_case(value, expected)
    pids = [v.get("pid") for v in values]
    require(
        all(type(pid) is int and pid > 0 and pid != os.getpid() for pid in pids)
        and len(set(pids)) == 3,
        "not fresh processes",
    )
    for key in ("snapshot", "binding_sha256", "configuration_sha256", "source_sha256"):
        require(
            loser[key] == winner[key] == recovered[key], "ownership binding changed"
        )
    for key in (
        "candidate_sha256",
        "ledger_sha256",
        "attempts_sha256",
        "stored_report",
    ):
        require(winner[key] == recovered[key], "recovery changed evidence")
    return [
        {key: value for key, value in receipt.items() if key != "pid"}
        for receipt in values
    ]


def orchestrate(directory: Path) -> list[dict[str, Any]]:
    fresh: Any = importlib.import_module("tools.dspy_fresh_process")
    children: list[Any] = []
    with contextlib.ExitStack() as captures:

        def launch(role: str) -> Any:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).resolve()),
                    "--worker",
                    role,
                    "--directory",
                    str(directory),
                ],
                cwd=ROOT,
                env=fresh.child_environment(directory / role),
                stdout=captures.enter_context(tempfile.TemporaryFile()),
                stderr=captures.enter_context(tempfile.TemporaryFile()),
            )
            children.append(child)
            return child

        try:
            launch("left")
            launch("right")

            def ready() -> bool:
                require(
                    all(child.poll() is None for child in children),
                    "contender exited before barrier",
                )
                return all(
                    (directory / (role + ".ready")).exists()
                    for role in ("left", "right")
                )

            wait_for(ready)
            (directory / "start").touch()

            def denied() -> bool:
                require(
                    all(child.poll() in (None, 0) for child in children),
                    "contender failed",
                )
                return (directory / "loser.json").exists()

            wait_for(denied)
            loser = read_json(directory / "loser.json")
            validate_case(loser, "denied")
            require(loser["source_sha256"] == sources(), "loser source changed")
            reserved = read_json(directory / "reserved.json")
            require(
                all(
                    loser[key] == reserved[key] == value
                    for key, value in persisted(directory).items()
                ),
                "pending reservation changed before release",
            )
            (directory / "release").touch()
            for child in children:
                require(child.wait(timeout=25) == 0, "contender failed")
            pair = [
                read_json(directory / (role + ".json")) for role in ("left", "right")
            ]
            require(
                all(value["pid"] == child.pid for value, child in zip(pair, children)),
                "worker PID does not match launched process",
            )
            require(loser in pair, "loser receipt changed")
            winners = [value for value in pair if value["outcome"] == "compiled"]
            require(
                len(winners) == 1
                and sum(value["outcome"] == "denied" for value in pair) == 1,
                "multiple owners",
            )
            require(launch("recover").wait(timeout=25) == 0, "recovery failed")
            recovered = read_json(directory / "recover.json")
            require(
                recovered["pid"] == children[-1].pid,
                "recovery PID does not match launched process",
            )
            result = [loser, winners[0], recovered]
            require(
                all(value["source_sha256"] == sources() for value in result),
                "child source changed",
            )
            return validate_results(result)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)


def run() -> dict[str, Any]:
    with isolated():
        storage: Any = importlib.import_module("refinery.storage")
        fixture: Any = importlib.import_module("test_durable")
        with tempfile.TemporaryDirectory(prefix="dspy-ownership-") as temporary:
            directory = Path(temporary)
            with storage.Store(directory / "store.sqlite") as store:
                snapshot, _, _ = fixture.setup(store)
            write_json(directory / "snapshot.json", snapshot)
            result = orchestrate(directory)
        return {
            "status": "passed",
            "evidence_class": "actual_dspy_two_process_ownership",
            "phases": result,
            "live_provider": "not_run",
            "promotion": "not_authorized",
            "reflection": "not_qualified",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("left", "right", "recover"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.worker:
        require(args.directory is not None, "worker directory missing")
        worker(args.directory, args.worker)
    else:
        print(json.dumps(run(), sort_keys=True))
