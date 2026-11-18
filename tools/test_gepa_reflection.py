"""Actual original GEPA / Predict synthetic qualification; no framework doubles."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import re
import socket
import sys
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
INSTRUCTION = "Read the source carefully and return the exact expected summary."
STAGES = ["optimization", "optimization", "reflection", "optimization", "optimization"]


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


class BoundedLog(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.bytes_written = 0

    def write(self, text: str) -> int:
        self.bytes_written += len(text.encode())
        if self.bytes_written > 1024 * 1024:
            raise ValueError("GEPA framework log limit")
        return super().write(text)


def validate(report: dict[str, Any]) -> None:
    require(
        all(
            type(report[key]) is int
            for key in (
                "compile_entries",
                "reflection_predict_entries",
                "new_dispatches",
                "network_attempts",
                "initial_task_failures",
                "changed_task_successes",
            )
        ),
        "GEPA invalid count types",
    )
    require(
        all(
            type(report["usage"][key]) is int
            for key in ("calls", "tokens", "cost_microunits", "unresolved_calls")
        ),
        "GEPA invalid accounting types",
    )
    require(
        report["status"] == "compiled" and report["reason"] is None,
        "GEPA compilation blocked",
    )
    require(
        report["compile_entries"] == report["reflection_predict_entries"] == 1,
        "original GEPA/reflection entry missing",
    )
    require(
        report["stages"] == STAGES and report["new_dispatches"] == 5,
        "GEPA dispatch sequence changed",
    )
    require(
        report["network_attempts"] == 0 and report["train_only"] is True,
        "GEPA forbidden effect",
    )
    require(
        report["observations_match_receipts"] is True,
        "GEPA original provider receipt mismatch",
    )
    require(
        report["selected_changed_instruction"] is True
        and report["reflection_output_matches_selected"] is True,
        "GEPA did not select metered reflection",
    )
    require(
        report["usage"]["calls"] == 5
        and report["usage"]["tokens"] == 5
        and report["usage"]["cost_microunits"] == 5
        and report["usage"]["unresolved_calls"] == 0
        and report["usage"]["reason"] is None,
        "GEPA accounting changed",
    )
    require(
        report["initial_task_failures"] == report["changed_task_successes"] == 2,
        "GEPA task evidence changed",
    )


def observe_completion(
    original: Any, observed: list[Any], instance: Any, request: Any
) -> Any:
    # Count entry before the original call: a throwing provider remains an effect.
    index = len(observed)
    observed.append((request, None))
    reply = original(instance, request)
    observed[index] = (
        request,
        hashlib.sha256(reply.content).hexdigest()
        if reply.content is not None
        else None,
    )
    return reply


def compile_diagnostic(exc: BaseException) -> dict[str, Any]:
    """Bounded synthetic-run exception metadata, without locals or source text."""
    # Do not invoke arbitrary argument repr/str implementations while reporting.
    args = [value[:1024] for value in exc.args[:4] if type(value) is str]
    message = " | ".join(args)
    message = re.sub(r"(?:[A-Za-z]:)?[/\\][^\s'\"<>]+", "<path>", message)
    message = "".join(c if 32 <= ord(c) < 127 else " " for c in message)[:512]
    frames: list[dict[str, Any]] = []
    cursor = exc.__traceback__
    traversed = 0
    while cursor is not None and traversed < 64:
        code = cursor.tb_frame.f_code
        filename = code.co_filename
        admitted = filename.startswith(str(ROOT) + "/") or filename.startswith("/deps/")
        frames.append(
            {
                "file": Path(filename).name[:96] if admitted else "<external>",
                "line": cursor.tb_lineno,
                "function": code.co_name[:96] if admitted else "<external>",
            }
        )
        cursor = cursor.tb_next
        traversed += 1
    return {
        "exception_type": type(exc).__name__[:96],
        "message": message,
        "frames": frames[-12:],
        "trace_truncated": cursor is not None or traversed > 12,
    }


def safe_compile_diagnostic(exc: BaseException) -> dict[str, Any]:
    try:
        return compile_diagnostic(exc)
    except BaseException:
        return {"diagnostic_capture_failed": True}


def observe_compile(
    original: Any,
    instance: Any,
    *args: Any,
    observations: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> Any:
    if observations is not None:
        retain_observation(observations, {"stage": "original_compile_entry"})
    try:
        result = original(instance, *args, **kwargs)
        if observations is not None:
            retain_observation(observations, {"stage": "original_compile_return"})
        return result
    except BaseException as exc:
        if observations is not None:
            retain_observation(
                observations,
                {
                    "stage": "original_compile_exception",
                    "exception": safe_compile_diagnostic(exc),
                },
            )
        # Diagnostics cannot authorize success or replace the original failure.
        try:
            print(
                "GEPA_COMPILE_EXCEPTION "
                + json.dumps(safe_compile_diagnostic(exc), sort_keys=True),
                file=sys.__stderr__,
            )
        except BaseException:
            pass
        raise


def retain_observation(
    observations: list[dict[str, Any]], value: dict[str, Any]
) -> None:
    if len(observations) == 12:
        del observations[0]
    observations.append(value)


def inspect_compile_result(result: Any, observations: list[dict[str, Any]]) -> Any:
    try:
        instruction = result.predict.signature.instructions
    except BaseException as exc:
        retain_observation(
            observations,
            {
                "stage": "wrapper_inspection_exception",
                "exception": safe_compile_diagnostic(exc),
            },
        )
        raise
    retain_observation(observations, {"stage": "wrapper_inspection_return"})
    return instruction


@contextlib.contextmanager
def observe_optimizer_exceptions(
    function: Any, observations: list[dict[str, Any]]
) -> Any:
    """Observe only this exact optimize code's exception events; never inspect locals."""
    code = function.__code__
    filename = str(ROOT / "refinery" / "gepa_optimizer.py")
    require(
        code.co_name == "optimize" and code.co_filename == filename,
        "unexpected optimizer trace target",
    )
    previous = sys.gettrace()

    def trace(frame: Any, event: str, arg: Any) -> Any:
        if frame.f_code is not code or frame.f_code.co_filename != filename:
            return None
        if event == "exception":
            _, exc, _ = arg
            retain_observation(
                observations,
                {
                    "stage": "optimizer_exception_event",
                    "line": frame.f_lineno,
                    "exception": safe_compile_diagnostic(exc),
                },
            )
        return trace

    try:
        sys.settrace(trace)
        yield
    finally:
        sys.settrace(previous)


def observe_worker_wrapper(
    original_wrap: Any,
    instance: Any,
    user_function: Any,
    observations: list[dict[str, Any]],
    lock: Any,
) -> Any:
    """Keep original safe_func/executor handling; observe immediately before it catches."""

    def record(value: dict[str, Any]) -> None:
        with lock:
            retain_observation(observations, value)

    def observed_user(item: Any) -> Any:
        worker = threading.current_thread() is not threading.main_thread()
        record({"stage": "worker_user_entry", "worker_thread": worker})
        try:
            result = user_function(item)
        except BaseException as exc:
            record(
                {
                    "stage": "worker_user_exception",
                    "worker_thread": worker,
                    "exception": safe_compile_diagnostic(exc),
                }
            )
            raise
        record({"stage": "worker_user_return", "worker_thread": worker})
        return result

    return original_wrap(instance, observed_user)


def once(forbidden: list[str]) -> dict[str, Any]:
    from refinery import evaluation as ev
    from refinery import gepa_codec as codec
    from refinery import gepa_optimizer as opt
    from refinery import gepa_runtime as runtime
    from refinery.durable_evaluation import SQLiteBudget
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
        tempfile.TemporaryDirectory() as directory,
        storage.Store(Path(directory) / "store.sqlite") as store,
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
        budget: Any = SQLiteBudget(
            store,
            "gepa-reflection",
            ev.Limits(16, 100, 1000, 100),
            clock_ms=clock,
            experiment="gepa-reflection",
        )
        with (
            patch.object(dspy.GEPA, "compile", compile_call),
            patch.object(parallelizer.ParallelExecutor, "_wrap_function", worker_wrap),
            patch.object(dspy.Predict, "forward", predict_call),
            patch.object(ev.ScriptedProvider, "complete", provider_call),
            observe_optimizer_exceptions(opt.optimize, optimizer_exceptions),
        ):
            result = opt.optimize(
                dataset,
                split,
                training_output_id=train.output.output_id,
                provider=provider,
                budget=budget,
                per_call=ev.Reservation(10, 10),
                check_use=lambda: None,
            )
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
        return report


def source_hashes() -> dict[str, str]:
    from refinery import gepa_runtime as runtime

    paths = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "refinery").glob("*.py")
    )
    paths += [
        "tools/offline_gepa.py",
        "tools/test_gepa_reflection.py",
        "tests/test_durable.py",
        "tests/test_lineage.py",
    ]
    return {p: runtime.digest(runtime.source_bytes(p)) for p in paths}


def run() -> dict[str, Any]:
    forbidden: list[str] = []

    def deny(*_: Any, **__: Any) -> Any:
        forbidden.append("network")
        raise ValueError("GEPA network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
        contextlib.redirect_stdout(BoundedLog()),
        contextlib.redirect_stderr(BoundedLog()),
    ):
        first, second = once(forbidden), once(forbidden)
        require(first == second, "GEPA repeated execution changed")
        require(not forbidden, "GEPA attempted network")
    from refinery import gepa_runtime as runtime

    return {
        "schema": "runspindle.gepa313-native.v1",
        "status": "passed",
        "repeat_equal": True,
        "runs": [first, second],
        "adoption_sha256": runtime.ADOPTION_SHA256,
        "sources": source_hashes(),
        "scope": "process-local synthetic GEPA reflection; no durable, heldout or production claim",
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
