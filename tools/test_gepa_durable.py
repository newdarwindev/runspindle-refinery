"""Actual durable GEPA compilation and import-forbidden fresh-process recovery.

Component tests validate orchestration/oracles only; native execution is separate.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.abc
import json
import os
import socket
import sys
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from tools.test_gepa_reflection import (  # noqa: E402
    INSTRUCTION,
    BoundedLog,
    inspect_compile_result,
    observe_compile,
    observe_completion,
    observe_optimizer_exceptions,
    observe_worker_wrapper,
    require,
    validate,
)
from tools.test_gepa_reflection import (  # noqa: E402
    source_hashes as base_sources,
)

CHILD_SECONDS = 50  # Two sequential children, within unchanged 120s container cap.


def persisted(store: Any) -> str:
    from refinery.lineage import digest
    from refinery.storage import pack

    # Logical exact stored bytes, not physical SQLite/WAL layout.
    tables = {}
    for table in ("objects", "budgets", "experiments", "attempts"):
        tables[table] = store.db.execute(
            "SELECT * FROM " + table + " ORDER BY 1,2"
        ).fetchall()
    return digest(pack(tables))


def identity(session: Any, result: Any) -> dict[str, Any]:
    from refinery.lineage import digest
    from refinery.storage import pack

    witnesses = session.store.db.execute(
        "SELECT identity,digest,payload FROM objects WHERE kind='gepa_request_witness' ORDER BY identity"
    ).fetchall()
    require(len(witnesses) == 5, "durable witness count")
    return {
        "snapshot": session.snapshot_sha256,
        "configuration": session.config,
        "binding": session.binding_sha256,
        "candidate": result.candidate_sha256,
        "result": digest(pack(result)),
        "attempts": digest(pack(result.attempts)),
        "witnesses": digest(pack(witnesses)),
        "usage": dict(result.usage),
        "clock_ms": session.clock(),
    }


def compile_child(path: Path, forbidden: list[str]) -> dict[str, Any]:
    from refinery import evaluation as ev
    from refinery import gepa_codec as codec
    from refinery import gepa_optimizer as opt
    from refinery import gepa_runtime as runtime
    from refinery.durable_dspy import Session
    from refinery.lineage import digest

    runtime_id = runtime.runtime_identity()
    dspy: Any = importlib.import_module("dspy")
    storage: Any = importlib.import_module("refinery.storage")
    fixture: Any = importlib.import_module("test_durable").setup
    compiled_instructions: list[str] = []
    reflected_instructions: list[str] = []
    compile_entries: list[bool] = []
    compile_observations: list[dict[str, Any]] = []
    optimizer_exceptions: list[dict[str, Any]] = []
    worker_observations: list[dict[str, Any]] = []
    worker_lock = threading.Lock()
    parallelizer: Any = importlib.import_module("dspy.utils.parallelizer")
    original_wrap = parallelizer.ParallelExecutor._wrap_function

    def worker_wrap(instance: Any, user_function: Any) -> Any:
        return observe_worker_wrapper(
            original_wrap, instance, user_function, worker_observations, worker_lock
        )

    reflection_entries: list[bool] = []
    observed: list[tuple[ev.Request, str | None]] = []
    original_compile = dspy.GEPA.compile
    original_predict = dspy.Predict.forward
    original_complete = ev.ScriptedProvider.complete

    def compile_call(instance: Any, *args: Any, **kwargs: Any) -> Any:
        compile_entries.append(True)
        result = observe_compile(
            original_compile,
            instance,
            *args,
            observations=compile_observations,
            **kwargs,
        )
        compiled_instructions.append(
            inspect_compile_result(result, compile_observations)
        )
        return result

    def predict_call(instance: Any, *args: Any, **kwargs: Any) -> Any:
        reflection = set(instance.signature.input_fields) == {"evidence"}
        if reflection:
            reflection_entries.append(True)
        result = original_predict(instance, *args, **kwargs)
        if reflection:
            reflected_instructions.append(result.instruction)
        return result

    def provider_call(instance: Any, request: ev.Request) -> Any:
        return observe_completion(original_complete, observed, instance, request)

    with (
        storage.Store(path / "store.sqlite") as store,
    ):
        snapshot, _, _ = fixture(store)
        dataset, split = store.lineage(snapshot)
        train = next(
            e
            for e in dataset.examples
            if split.partition(e.source.family_id) == "train"
        )
        clock: Any = ev.VirtualClock()
        provider: Any = ev.ScriptedProvider(
            tuple(
                ev.Completion(value, 1, 1, True, 0)
                for value in (
                    b"wrong-initial",
                    b"wrong-initial",
                    INSTRUCTION.encode(),
                    train.gold,
                    train.gold,
                )
            ),
            clock=clock,
        )
        session: Any = Session(
            store,
            storage.VERSION,
            "gepa-durable",
            snapshot,
            provider=provider,
            limits=ev.Limits(16, 100, 1000, 100),
            per_call=ev.Reservation(10, 10),
            budget_id="gepa-durable",
            strategy="gepa",
            training_output_id=train.output.output_id,
            synthetic_fixture=True,
        )
        budget = session.budget()
        with (
            patch.object(dspy.GEPA, "compile", compile_call),
            patch.object(parallelizer.ParallelExecutor, "_wrap_function", worker_wrap),
            patch.object(dspy.Predict, "forward", predict_call),
            patch.object(ev.ScriptedProvider, "complete", provider_call),
            observe_optimizer_exceptions(opt.optimize, optimizer_exceptions),
        ):
            result = session.compile()
        decoded = None
        if result.candidate is not None and result.candidate_sha256 is not None:
            decoded = codec.decode(
                result.candidate,
                result.candidate_sha256,
                dataset,
                split,
                result.configuration_sha256,
                result.attempts,
            )
        report = {
            "status": result.status,
            "reason": result.reason,
            "runtime_identity": runtime_id,
            "compile_entries": len(compile_entries),
            "reflection_predict_entries": len(reflection_entries),
            "new_dispatches": len(observed),
            "stages": [r.stage for r, _ in observed],
            "network_attempts": len(forbidden),
            "train_only": all(
                (r.output_id, r.output_revision, r.source_sha256, r.split_sha256)
                == (
                    train.output.output_id,
                    train.output.revision,
                    train.source.sha256,
                    split.sha256,
                )
                for r, _ in observed
            ),
            "observations_match_receipts": observed
            == [(a.request, a.content_sha256) for a in result.attempts],
            "selected_changed_instruction": compiled_instructions == [INSTRUCTION]
            and decoded == INSTRUCTION
            and decoded != codec.INITIAL,
            "reflection_output_matches_selected": reflected_instructions
            == compiled_instructions
            == [INSTRUCTION],
            "initial_task_failures": sum(
                r.stage == "optimization" and content == digest(b"wrong-initial")
                for r, content in observed
            ),
            "changed_task_successes": sum(
                r.stage == "optimization" and content == digest(train.gold)
                for r, content in observed
            ),
            "usage": budget.snapshot(),
            "configuration_sha256": result.configuration_sha256,
            "candidate_sha256": result.candidate_sha256,
            "attempts_sha256": digest(
                runtime.canonical([asdict(a) for a in result.attempts])
            ),
        }
        try:
            validate(report)
        except ValueError:
            # Bounded synthetic-only observations survive failure, never a success receipt.
            print(
                "GEPA_DIAGNOSTIC "
                + json.dumps(
                    {
                        **report,
                        "compile_observations": compile_observations,
                        "optimizer_exceptions": optimizer_exceptions,
                        "worker_observations": worker_observations,
                    },
                    sort_keys=True,
                ),
                file=sys.__stderr__,
            )
            raise
        return {
            "pid": os.getpid(),
            "phase": "compile",
            "framework": report,
            "snapshot": snapshot,
            "identity": identity(session, result),
            "persisted": persisted(store),
            "network_attempts": len(forbidden),
        }


class RejectFramework(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname.split(".")[0] in {"dspy", "gepa"}:
            self.attempts.append(fullname)
            raise ValueError("framework import forbidden during recovery")
        return None


def recover_child(
    path: Path, first: dict[str, Any], forbidden: list[str]
) -> dict[str, Any]:
    from refinery import evaluation as ev
    from refinery.durable_dspy import Session
    from refinery.storage import VERSION, Store

    require(
        not any(n.split(".")[0] in {"dspy", "gepa"} for n in sys.modules),
        "fresh interpreter required",
    )
    reject = RejectFramework()
    calls: list[bool] = []

    def deny_complete(*_: Any, **__: Any) -> Any:
        calls.append(True)
        raise ValueError("recovery provider effect forbidden")

    sys.meta_path.insert(0, reject)
    try:
        with Store(path / "store.sqlite") as store:
            before = persisted(store)
            require(before == first["persisted"], "checkpoint changed before recovery")
            dataset, split = store.lineage(first["snapshot"])
            train = next(
                e
                for e in dataset.examples
                if split.partition(e.source.family_id) == "train"
            )
            provider = ev.ScriptedProvider(
                tuple(
                    ev.Completion(v, 1, 1, True, 0)
                    for v in (
                        b"wrong-initial",
                        b"wrong-initial",
                        INSTRUCTION.encode(),
                        train.gold,
                        train.gold,
                    )
                ),
                clock=ev.VirtualClock(),
            )
            session: Any = Session(
                store,
                VERSION,
                "gepa-durable",
                first["snapshot"],
                provider=provider,
                limits=ev.Limits(16, 100, 1000, 100),
                per_call=ev.Reservation(10, 10),
                budget_id="gepa-durable",
                strategy="gepa",
                training_output_id=train.output.output_id,
                synthetic_fixture=True,
            )
            with patch.object(ev.ScriptedProvider, "complete", deny_complete):
                result = session.compile()
            require(
                not calls and not reject.attempts, "recovery attempted effects/import"
            )
            after = persisted(store)
            require(before == after, "recovery changed persisted checkpoint")
            return {
                "pid": os.getpid(),
                "phase": "recover",
                "identity": identity(session, result),
                "persisted": after,
                "new_dispatches": len(calls),
                "framework_imports": len(reject.attempts),
                "network_attempts": len(forbidden),
            }
    finally:
        sys.meta_path.remove(reject)


def validate_pair(first: dict[str, Any], second: dict[str, Any]) -> None:
    validate(first["framework"])
    require(
        first.get("phase") == "compile" and second.get("phase") == "recover",
        "missing durable phase",
    )
    require(
        type(first.get("pid")) is int
        and type(second.get("pid")) is int
        and first["pid"] > 0
        and second["pid"] > 0
        and first["pid"] != second["pid"],
        "distinct fresh processes required",
    )
    require(
        first["identity"] == second["identity"]
        and first["persisted"] == second["persisted"],
        "durable recovery identity changed",
    )
    require(
        first["identity"]["candidate"] == first["framework"]["candidate_sha256"]
        and first["identity"]["configuration"]
        == first["framework"]["configuration_sha256"],
        "framework checkpoint association changed",
    )
    require(first["network_attempts"] == 0, "compile network effect")
    for field in ("new_dispatches", "framework_imports", "network_attempts"):
        require(type(second[field]) is int and second[field] == 0, "recovery effect")


def child(phase: str, path: Path) -> dict[str, Any]:
    forbidden: list[str] = []

    def deny(*_: Any, **__: Any) -> Any:
        forbidden.append("network")
        raise ValueError("durable GEPA network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
        contextlib.redirect_stdout(BoundedLog()),
        contextlib.redirect_stderr(BoundedLog()),
    ):
        if phase == "compile":
            result = compile_child(path, forbidden)
        elif phase == "recover":
            raw = (path / "compile.json").read_bytes()
            require(len(raw) <= 65536, "compile receipt bound")
            result = recover_child(path, json.loads(raw), forbidden)
        else:
            raise ValueError("unknown durable phase")
        require(not forbidden, "network attempt")
        return result


def run_child(phase: str, directory: Path) -> dict[str, Any]:
    # Explicit allowlist: no inherited credentials, caller Python paths or model settings.
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": "/deps:/work",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "HOME": str(directory),
        "DSPY_CACHEDIR": str(directory / "cache"),
        "HF_HUB_OFFLINE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    # Same bounded pipe/kill/reap implementation as the sealed launcher, extended
    # only with explicit environment support. No shell or unbounded communicate.
    from tools.offline_gepa import bounded

    result = bounded(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--child",
            phase,
            str(directory),
        ],
        CHILD_SECONDS,
        env=env,
    )
    require(result.returncode == 0, "durable child failed: " + result.stderr[-4096:])
    value = json.loads(result.stdout)
    require(type(value) is dict, "invalid durable child receipt")
    return cast(dict[str, Any], value)


def source_hashes() -> dict[str, str]:
    from refinery import gepa_runtime as runtime

    return {
        **base_sources(),
        "tools/test_gepa_durable.py": runtime.digest(
            runtime.source_bytes("tools/test_gepa_durable.py")
        ),
    }


def validate_report(report: dict[str, Any]) -> None:
    from refinery import gepa_runtime as runtime

    require(
        report.get("schema") == "runspindle.gepa313-durable.v1"
        and report.get("status") == "passed",
        "durable report status",
    )
    require(
        report.get("sources") == source_hashes()
        and report.get("adoption_sha256") == runtime.ADOPTION_SHA256,
        "durable source binding",
    )
    validate_pair(report["compile"], report["recover"])


def run() -> dict[str, Any]:
    from refinery import gepa_runtime as runtime

    sources = source_hashes()
    with tempfile.TemporaryDirectory(prefix="gepa-durable-") as temp:
        directory = Path(temp)
        first = run_child("compile", directory)
        with (directory / "compile.json").open("x") as receipt:
            json.dump(first, receipt, sort_keys=True)
        second = run_child("recover", directory)
        validate_pair(first, second)
    require(sources == source_hashes(), "durable source changed")
    report = {
        "schema": "runspindle.gepa313-durable.v1",
        "status": "passed",
        "compile": first,
        "recover": second,
        "sources": sources,
        "adoption_sha256": runtime.ADOPTION_SHA256,
        "scope": "actual compile and fresh-process checkpoint recovery only; no heldout evaluation",
    }
    validate_report(report)
    return report


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        print(json.dumps(child(sys.argv[2], Path(sys.argv[3])), sort_keys=True))
    elif len(sys.argv) == 1:
        print(json.dumps(run(), sort_keys=True))
    else:
        raise ValueError("invalid durable harness arguments")
