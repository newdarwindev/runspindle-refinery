"""Actual-framework candidate/result integrity and heldout mutation qualification."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
shared: Any = importlib.import_module("tools.test_dspy_ownership")


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def check_result(value: dict[str, Any]) -> None:
    require(
        value["compile_entries"]
        == value["new_dispatches"]
        == value["settled_calls"]
        == 1,
        "result fixture execution changed",
    )
    require(
        value["train_only"] is True and value["network_attempts"] == 0,
        "result forbidden effect",
    )
    require(
        value["ledger_calls"] == 1 and value["unresolved_calls"] == 0,
        "result charges changed",
    )
    require(value["mutated"] is True, "result challenge missing")
    require(
        value["denial"] == "DSPy result identity changed"
        and value["state"] == "compiling"
        and value["compiled_objects"] == 0
        and value["returned_sha256"] is None,
        "result tamper escaped",
    )


def check_allowlist(value: dict[str, Any]) -> None:
    require(value["compile_entries"] == 1, "allowlist actual compile missing")
    require(
        value["candidate_present"] is False and value["status"] == "blocked",
        "allowlist candidate escaped",
    )
    require(value["network_attempts"] == 0, "allowlist network effect")
    require(
        value["new_dispatches"] == 0
        and value["heldout_dispatches"] == 0
        and value["ledger_calls"] == 0,
        "heldout request crossed boundary",
    )


def check_locked(value: dict[str, Any]) -> None:
    require(
        value["compile_entries"] == 1 and value["compile_dispatches"] == 1,
        "locked fixture actual compile missing",
    )
    require(
        value["original_status"] == "compiled" and value["candidate_changed"] is True,
        "locked challenge missing",
    )
    require(
        value["denial"] == "locked candidate digest changed"
        and value["new_evaluation_dispatches"] == 0
        and value["ledger_unchanged"] is True
        and value["attempts_unchanged"] is True,
        "locked candidate tamper escaped",
    )
    require(
        value["train_only"] is True and value["network_attempts"] == 0,
        "locked forbidden effect",
    )


def detected(
    oracle: Callable[[dict[str, Any]], None], value: dict[str, Any], expected: str
) -> None:
    try:
        oracle(value)
    except ValueError as error:
        require(str(error) == expected, "mutation failed for unrelated reason")
    else:
        raise ValueError("broken guard escaped independent oracle")


def observed_check(
    name: str, oracle: Callable[[dict[str, Any]], None], value: dict[str, Any]
) -> None:
    try:
        oracle(value)
    except ValueError:
        keys = {
            "compile_entries",
            "compile_dispatches",
            "new_dispatches",
            "settled_calls",
            "ledger_calls",
            "unresolved_calls",
            "train_only",
            "network_attempts",
            "mutated",
            "denial",
            "state",
            "compiled_objects",
            "returned_sha256",
            "original_sha256",
            "stored_sha256",
            "candidate_present",
            "status",
            "reason",
            "heldout_dispatches",
            "original_status",
            "candidate_changed",
            "new_evaluation_dispatches",
            "ledger_unchanged",
            "attempts_unchanged",
        }

        def safe(item: Any) -> Any:
            if item is None or type(item) is bool:
                return item
            if type(item) is int and 0 <= item <= 2**64 - 1:
                return item
            if (
                type(item) is str
                and len(item) <= 96
                and all(
                    c
                    in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-:."
                    for c in item
                )
            ):
                return item
            return "redacted"

        print(
            json.dumps(
                {
                    "diagnostic": "failed_integrity_oracle",
                    "case": name,
                    "observed": {
                        key: safe(value[key]) for key in sorted(keys & value.keys())
                    },
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise


@contextlib.contextmanager
def observations(ev: Any, dspy: Any) -> Any:
    calls: list[Any] = []
    entries: list[str] = []
    complete = ev.ScriptedProvider.complete
    compile_original = dspy.BootstrapFewShot.compile

    def observe(instance: Any, request: Any) -> Any:
        calls.append(request)
        return complete(instance, request)

    def compile_actual(instance: Any, *args: Any, **kwargs: Any) -> Any:
        entries.append("compile")
        return compile_original(instance, *args, **kwargs)

    with (
        patch.object(ev.ScriptedProvider, "complete", observe),
        patch.object(dspy.BootstrapFewShot, "compile", compile_actual),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        yield calls, entries


def ledger_bytes(store: Any) -> bytes:
    rows = store.db.execute(
        "SELECT identity,payload FROM budgets ORDER BY identity"
    ).fetchall()
    return json.dumps(
        [(identity, payload.hex()) for identity, payload in rows]
    ).encode()


def attempt_bytes(store: Any) -> bytes:
    rows = store.db.execute(
        "SELECT experiment,ticket,request,outcome,receipt FROM attempts ORDER BY experiment,ticket"
    ).fetchall()
    return json.dumps(
        [
            [value.hex() if type(value) is bytes else value for value in row]
            for row in rows
        ]
    ).encode()


def session_for(
    store: Any, fixture: Any, ev: Any, opt: Any, lifecycle: Any, storage: Any
) -> Any:
    snapshot, _, _ = fixture.setup(store)
    dataset, split = store.lineage(snapshot)
    train, heldout = opt.validate_dataset(dataset, split)
    script = (ev.Completion(train[0].gold, 1, 1, True, 0),) + tuple(
        ev.Completion(e.gold, 1, 1, True, 0) for _ in range(2) for e in heldout
    )
    provider = ev.ScriptedProvider(script, clock=ev.VirtualClock())
    return lifecycle.Session(
        store,
        storage.VERSION,
        "integrity",
        snapshot,
        provider=provider,
        limits=ev.Limits(100, 1000, 1000, 1000),
        per_call=ev.Reservation(10, 10),
        budget_id="integrity-shared",
        settings=opt.Settings(1, 1),
        synthetic_fixture=True,
    )


def run() -> dict[str, Any]:
    with shared.isolated() as forbidden:
        ev: Any = importlib.import_module("refinery.evaluation")
        opt: Any = importlib.import_module("refinery.dspy_optimizer")
        durable: Any = importlib.import_module("refinery.durable_evaluation")
        lifecycle: Any = importlib.import_module("refinery.durable_dspy")
        storage: Any = importlib.import_module("refinery.storage")
        fixture: Any = importlib.import_module("test_durable")
        dspy = opt.framework()
        results: dict[str, Any] = {}
        with tempfile.TemporaryDirectory(prefix="dspy-integrity-") as temporary:
            directory = Path(temporary)
            with storage.Store(directory / "locked.sqlite") as store:
                session = session_for(store, fixture, ev, opt, lifecycle, storage)
                with observations(ev, dspy) as (calls, entries):
                    compiled = session.compile()
                    require(
                        compiled.status == "compiled", "actual locked compile failed"
                    )
                    before = ledger_bytes(store)
                    before_attempts = attempt_bytes(store)
                    compile_calls = len(calls)
                    altered = compiled.candidate + b" "
                    denial = None
                    try:
                        opt.evaluate_locked(
                            altered,
                            expected_sha256=compiled.candidate_sha256,
                            configuration_sha256=compiled.configuration_sha256,
                            settings=session.settings,
                            dataset=session.dataset,
                            split=session.split,
                            provider=session.provider,
                            budget=session.budget(),
                            per_call=session.per_call,
                            check_use=lambda: session.guard("evaluation"),
                        )
                    except ValueError as error:
                        denial = str(error)
                locked = {
                    "compile_entries": len(entries),
                    "compile_dispatches": compile_calls,
                    "original_status": compiled.status,
                    "candidate_changed": shared.digest(altered)
                    != compiled.candidate_sha256,
                    "denial": denial,
                    "new_evaluation_dispatches": len(calls) - compile_calls,
                    "ledger_unchanged": ledger_bytes(store) == before,
                    "attempts_unchanged": attempt_bytes(store) == before_attempts,
                    "train_only": all(
                        r.stage == "optimization"
                        and r.output_id in {e.output.output_id for e in session.train}
                        for r in calls
                    ),
                    "network_attempts": len(forbidden),
                    "candidate_sha256": compiled.candidate_sha256,
                    "configuration_sha256": compiled.configuration_sha256,
                    "ledger_sha256": shared.digest(before),
                }
                observed_check("locked_candidate", check_locked, locked)
                results["locked_candidate"] = locked
            for mutant in (False, True):
                with storage.Store(
                    directory / ("result-" + str(mutant) + ".sqlite")
                ) as store:
                    session = session_for(store, fixture, ev, opt, lifecycle, storage)
                    capture: dict[str, Any] = {}
                    commit = session.commit_compiled

                    def capture_commit(value: Any) -> None:
                        capture["result"] = value
                        capture["original_bytes"] = storage.pack(value)
                        commit(value)

                    def mutate(point: str) -> None:
                        if point == "before_dspy_compile_commit":
                            object.__setattr__(
                                capture["result"],
                                "candidate",
                                b"synthetic-corrupt-candidate",
                            )
                            object.__setattr__(
                                capture["result"],
                                "candidate_sha256",
                                shared.digest(b"synthetic-corrupt-candidate"),
                            )

                    session.commit_compiled = capture_commit
                    session.checkpoint = mutate
                    removal = (
                        patch.object(
                            session, "verify_result_identity", return_value=None
                        )
                        if mutant
                        else contextlib.nullcontext()
                    )
                    returned, denial = None, None
                    with removal, observations(ev, dspy) as (calls, entries):
                        try:
                            returned = session.compile()
                        except ValueError as error:
                            denial = str(error)
                    stored = store.get("dspy_compiled", session.key) if mutant else None
                    outcome = {
                        "compile_entries": len(entries),
                        "new_dispatches": len(calls),
                        "ledger_calls": session.budget().snapshot()["calls"],
                        "unresolved_calls": session.budget().snapshot()[
                            "unresolved_calls"
                        ],
                        "settled_calls": store.db.execute(
                            "SELECT COUNT(*) FROM attempts WHERE outcome='settled'"
                        ).fetchone()[0],
                        "train_only": all(
                            r.stage == "optimization"
                            and r.output_id
                            in {e.output.output_id for e in session.train}
                            for r in calls
                        ),
                        "network_attempts": len(forbidden),
                        "mutated": storage.pack(capture["result"])
                        != capture["original_bytes"],
                        "denial": denial,
                        "state": session.row(store.db)[1],
                        "compiled_objects": store.db.execute(
                            "SELECT COUNT(*) FROM objects WHERE kind='dspy_compiled'"
                        ).fetchone()[0],
                        "returned_sha256": None
                        if returned is None
                        else shared.digest(storage.pack(returned)),
                        "original_sha256": shared.digest(capture["original_bytes"]),
                        "stored_sha256": None
                        if stored is None
                        else shared.digest(storage.pack(stored["result"])),
                        "ledger_sha256": shared.digest(ledger_bytes(store)),
                    }
                    if mutant:
                        require(
                            denial is None
                            and outcome["state"] == "compiled"
                            and outcome["compiled_objects"] == 1,
                            "identity mutant did not reach divergent outward return",
                        )
                        require(
                            outcome["stored_sha256"] == outcome["original_sha256"]
                            and outcome["returned_sha256"] != outcome["stored_sha256"],
                            "identity mutant drift absent",
                        )
                        observed_check(
                            "result_guard_removed",
                            lambda value: detected(
                                check_result, value, "result tamper escaped"
                            ),
                            outcome,
                        )
                    else:
                        observed_check("result_guarded", check_result, outcome)
                    results["result_guard_removed" if mutant else "result_guarded"] = (
                        outcome
                    )
            for mutant in (False, True):
                with storage.Store(
                    directory / ("allowlist-" + str(mutant) + ".sqlite")
                ) as store:
                    snapshot, _, _ = fixture.setup(store)
                    dataset, split = store.lineage(snapshot)
                    train, heldout = opt.validate_dataset(dataset, split)
                    target = heldout[0]
                    provider = ev.ScriptedProvider(
                        (ev.Completion(target.gold, 1, 1, True, 0),),
                        clock=ev.VirtualClock(),
                    )
                    budget = durable.SQLiteBudget(
                        store,
                        "leak-budget",
                        ev.Limits(1, 1000, 1000, 1000),
                        clock_ms=provider.clock,
                        experiment="leak",
                    )
                    invoke = opt.Dispatch.invoke
                    initialize = opt.Dispatch.__init__

                    def challenge(instance: Any, predict: Any, value: str) -> Any:
                        # Identical heldout challenge with and without the single
                        # admission mutation; original framework/compiler unchanged.
                        return invoke(instance, predict, opt.case(target))

                    def admit(instance: Any, *args: Any, **kwargs: Any) -> None:
                        initialize(instance, *args, **kwargs)
                        instance.allowed[opt.case(target)] = target

                    admission_removal = (
                        patch.object(opt.Dispatch, "__init__", admit)
                        if mutant
                        else contextlib.nullcontext()
                    )
                    with (
                        admission_removal,
                        patch.object(opt.Dispatch, "invoke", challenge),
                        observations(ev, dspy) as (calls, entries),
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
                    outcome = {
                        "compile_entries": len(entries),
                        "candidate_present": result.candidate is not None,
                        "status": result.status,
                        "reason": result.reason,
                        "network_attempts": len(forbidden),
                        "new_dispatches": len(calls),
                        "heldout_dispatches": sum(
                            r.output_id == target.output.output_id
                            and r.output_revision == target.output.revision
                            for r in calls
                        ),
                        "ledger_calls": budget.snapshot()["calls"],
                        "ledger_sha256": shared.digest(ledger_bytes(store)),
                        "target_output_id": target.output.output_id,
                        "train_output_ids": sorted(e.output.output_id for e in train),
                    }
                    require(
                        outcome["target_output_id"] not in outcome["train_output_ids"],
                        "leak challenge is not heldout",
                    )
                    if mutant:
                        require(
                            outcome["new_dispatches"]
                            == outcome["heldout_dispatches"]
                            == outcome["ledger_calls"]
                            == 1,
                            "admission mutant leakage not observed",
                        )
                        observed_check(
                            "allowlist_guard_removed",
                            lambda value: detected(
                                check_allowlist,
                                value,
                                "heldout request crossed boundary",
                            ),
                            outcome,
                        )
                    else:
                        observed_check("allowlist_guarded", check_allowlist, outcome)
                    results[
                        "allowlist_guard_removed" if mutant else "allowlist_guarded"
                    ] = outcome
        require(not forbidden, "network attempted")
        return {
            "status": "passed",
            "evidence_class": "actual_framework_integrity_and_mutation",
            "cases": results,
            "source_sha256": {
                **shared.sources(),
                "tools/test_dspy_integrity.py": shared.digest(
                    Path(__file__).read_bytes()
                ),
            },
            "live_provider": "not_run",
            "reflection": "not_qualified",
            "promotion": "not_authorized",
        }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
