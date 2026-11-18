"""Actual signed mixed materialized GEPA compile, heldout evaluation and replay.

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
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from tools import test_gepa_heldout_materialized as h  # noqa: E402
from tools.test_gepa_heldout_materialized import (  # noqa: E402
    authority_receipt,
    identity,
    materialized_context,
    materialized_identity,
    persisted,
)
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

CASES = ("cancelled", "unknown_tokens", "unknown_cost", "calls_exhausted")
CHILD_SECONDS = 30
TETHER = Path("/tether")


def session_for(material: dict[str, Any], case: str) -> Any:
    from refinery import evaluation as ev
    from refinery.durable_dspy import Session
    from refinery.optimization import validate_dataset
    from refinery.storage import VERSION

    require(case in CASES, "unknown adversarial case")
    train, heldout = validate_dataset(*material["store"].lineage(material["snapshot"]))
    require(len(train) == len(heldout) == 1, "exact two-family split")
    script = (
        b"wrong-initial",
        b"wrong-initial",
        INSTRUCTION.encode(),
        train[0].gold,
        train[0].gold,
        b"wrong-heldout-baseline",
        heldout[0].gold,
    )
    return Session(
        material["store"],
        VERSION,
        "materialized-gepa-heldout",
        material["snapshot"],
        provider=ev.ScriptedProvider(
            tuple(
                ev.Completion(
                    v,
                    None if case == "unknown_tokens" and i == 5 else 1,
                    None if case == "unknown_cost" and i == 5 else 1,
                    True,
                    0,
                )
                for i, v in enumerate(script)
            ),
            clock=ev.VirtualClock(),
        ),
        limits=ev.Limits(6 if case == "calls_exhausted" else 16, 100, 1000, 100),
        per_call=ev.Reservation(10, 10),
        budget_id="materialized-gepa-heldout",
        strategy="gepa",
        training_output_id=train[0].output.output_id,
        retained_use=material["use"],
        materialized_batches=material["batches"],
    )


def compile_child(path: Path, forbidden: list[str], case: str) -> dict[str, Any]:
    from refinery import evaluation as ev
    from refinery import gepa_codec as codec
    from refinery import gepa_optimizer as opt
    from refinery import gepa_runtime as runtime
    from refinery.lineage import digest

    runtime_id = runtime.runtime_identity()
    dspy: Any = importlib.import_module("dspy")
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

    with materialized_context(path) as material:
        store, snapshot = material["store"], material["snapshot"]
        dataset, split = store.lineage(snapshot)
        train = next(
            e
            for e in dataset.examples
            if split.partition(e.source.family_id) == "train"
        )
        session = session_for(material, case)
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
            "materialized": materialized_identity(session, material),
            "authorization": authority_receipt(
                material, ("optimization", "reflection")
            ),
            "network_attempts": len(forbidden),
        }


def outcome(session: Any, report: Any) -> dict[str, Any]:
    from refinery.lineage import digest
    from refinery.storage import pack

    return dict(
        configuration=session.config,
        binding=session.binding_sha256,
        candidate=session.compiled()[0].candidate_sha256,
        state=session.row(session.store.db)[1],
        usage=session.budget().snapshot(),
        report=None if report is None else digest(pack(report)),
        status=None if report is None else report["status"],
        persisted=persisted(session.store),
        attempt_outcomes=[
            list(row)
            for row in session.store.db.execute(
                "SELECT outcome,count(*),sum(receipt IS NOT NULL) FROM attempts WHERE experiment=? GROUP BY outcome ORDER BY outcome",
                (session.key,),
            )
        ],
        heldout_attempts=[]
        if report is None
        else [asdict(a) for a in report["attempts"]],
    )


def denied_claim(session: Any) -> None:
    from refinery import evaluation as ev

    try:
        session.claim_evaluation()
    except ev.Halt as error:
        require(str(error) == "cancelled", "wrong cancellation reason")
    else:
        raise ValueError("cancelled evaluation claim admitted")


def evaluate_child(
    path: Path, first: dict[str, Any], forbidden: list[str], case: str
) -> dict[str, Any]:
    from refinery import evaluation as ev

    dspy: Any = importlib.import_module("dspy")
    original_predict = dspy.Predict.forward
    original_complete = ev.ScriptedProvider.complete
    observed: list[tuple[Any, str | None]] = []
    predictions: list[bool] = []
    forbidden_framework: list[bool] = []

    def deny_compile(*_: Any, **__: Any) -> Any:
        forbidden_framework.append(True)
        raise ValueError("evaluation compiler forbidden")

    def predict(instance: Any, *args: Any, **kwargs: Any) -> Any:
        if set(instance.signature.input_fields) == {"evidence"}:
            forbidden_framework.append(True)
            raise ValueError("evaluation reflection forbidden")
        predictions.append(True)
        return original_predict(instance, *args, **kwargs)

    def complete(instance: Any, request: Any) -> Any:
        return observe_completion(original_complete, observed, instance, request)

    with materialized_context(path) as material:
        session = session_for(material, case)
        require(
            persisted(session.store) == first["persisted"], "compile checkpoint changed"
        )
        require(
            materialized_identity(session, material) == first["materialized"],
            "owner changed",
        )
        report = None
        if case == "cancelled":
            session.budget().stop("cancelled")
        with (
            patch.object(dspy.GEPA, "compile", deny_compile),
            patch.object(dspy.Predict, "forward", predict),
            patch.object(ev.ScriptedProvider, "complete", complete),
        ):
            if case == "cancelled":
                # Actual evaluate entry; cancellation denies before claim/seal.
                try:
                    session.evaluate()
                except ev.Halt as error:
                    require(str(error) == "cancelled", "wrong initial cancellation")
                else:
                    raise ValueError("cancelled evaluation admitted")
            else:
                report = session.evaluate()
                require(report["status"] == "blocked", "adversarial evaluation escaped")
            current = outcome(session, report)
            if case == "cancelled":
                denied_claim(session)
            else:
                require(
                    session.evaluate() == report, "blocked report changed on replay"
                )
            require(outcome(session, report) == current, "retry mutated checkpoint")
        require(not forbidden_framework, "compile/reflection entered during heldout")
        require(
            len(observed) == (0 if case == "cancelled" else 1), "heldout dispatch count"
        )
        require(
            len(predictions)
            == (0 if case == "cancelled" else 2 if case == "calls_exhausted" else 1),
            "prediction entry count",
        )
        if report is not None:
            require(
                observed == [(a.request, a.content_sha256) for a in report["attempts"]],
                "receipt association",
            )
            require(h.train_denied(session), "sealed training reentered")
        return dict(
            phase="evaluate",
            pid=os.getpid(),
            outcome=current,
            materialized=materialized_identity(session, material),
            authorization=authority_receipt(material, ("evaluation",)),
            new_dispatches=len(observed),
            predict_entries=len(predictions),
            forbidden_framework=len(forbidden_framework),
            stages=[r.stage for r, _ in observed],
            retry_preserved=True,
            network_attempts=len(forbidden),
        )


def replay_child(
    path: Path, second: dict[str, Any], forbidden: list[str], case: str
) -> dict[str, Any]:
    require(
        not any(n.split(".")[0] in {"dspy", "gepa"} for n in sys.modules),
        "fresh interpreter",
    )
    reject = h.RejectFramework()
    effects: list[bool] = []

    def deny(*_: Any, **__: Any) -> Any:
        effects.append(True)
        raise ValueError("replay effect")

    sys.meta_path.insert(0, reject)
    try:
        from refinery import evaluation as ev

        with (
            patch.object(ev.ScriptedProvider, "complete", deny),
            materialized_context(path) as material,
        ):
            session = session_for(material, case)
            require(
                persisted(session.store) == second["outcome"]["persisted"],
                "replay input changed",
            )
            report = None
            if case == "cancelled":
                denied_claim(session)
            else:
                report = session.evaluate()
            current = outcome(session, report)
            require(current == second["outcome"], "fresh replay changed outcome")
            require(
                not effects and not reject.attempts and not session.provider.calls,
                "replay effects/imports",
            )
            return dict(
                phase="replay",
                pid=os.getpid(),
                outcome=current,
                materialized=materialized_identity(session, material),
                authorization=authority_receipt(material, ("evaluation",)),
                new_dispatches=0,
                framework_imports=0,
                network_attempts=len(forbidden),
            )
    finally:
        sys.meta_path.remove(reject)


def validate_phases(
    case: str, first: dict[str, Any], second: dict[str, Any], third: dict[str, Any]
) -> None:
    require(case in CASES, "unknown case")
    validate(first["framework"])
    h.validate_identity(first["identity"], compiled=True)
    require(
        first["materialized"] == second["materialized"] == third["materialized"],
        "owner changed",
    )
    require(
        first["authorization"] == {"optimization": True, "reflection": True},
        "compile grants",
    )
    require(
        second["authorization"] == third["authorization"] == {"evaluation": True},
        "evaluation grants",
    )
    require(
        [p["phase"] for p in (first, second, third)]
        == ["compile", "evaluate", "replay"],
        "phase sequence",
    )
    pids = [p["pid"] for p in (first, second, third)]
    require(
        all(type(p) is int and p > 0 for p in pids) and len(set(pids)) == 3,
        "fresh processes",
    )
    result = second["outcome"]
    require(result == third["outcome"], "replay outcome")
    for key in ("configuration", "binding", "candidate"):
        require(result[key] == first["identity"][key], "cross phase identity")
    cancelled = case == "cancelled"
    unknown = case.startswith("unknown_")
    expected = dict(
        calls=5 if cancelled else 6,
        tokens=None if unknown else 5 if cancelled else 6,
        cost_microunits=None if unknown else 5 if cancelled else 6,
        elapsed_ms=0,
        unresolved_calls=1 if unknown else 0,
        reason="cancelled"
        if cancelled
        else "unknown_or_invalid_usage"
        if unknown
        else "calls_exhausted",
    )
    require(result["usage"] == expected, "retained budget/unknown usage")
    require(
        all(type(result["usage"][k]) is type(v) for k, v in expected.items()),
        "usage scalar types",
    )
    require(
        result["attempt_outcomes"]
        == (
            [["settled", 5, 5]]
            if cancelled
            else [["settled", 5, 5], ["unknown", 1, 1]]
            if unknown
            else [["settled", 6, 6]]
        ),
        "durable reservation/receipt outcomes",
    )
    require(
        len(result["heldout_attempts"]) == (0 if cancelled else 1),
        "heldout receipt count",
    )
    if not cancelled:
        receipt = result["heldout_attempts"][0]
        require(receipt["request"]["stage"] == "baseline", "heldout receipt phase")
        require(
            receipt["tokens"] == (None if case == "unknown_tokens" else 1)
            and receipt["cost_microunits"] == (None if case == "unknown_cost" else 1),
            "unknown receipt retained",
        )
    require(
        set(first["materialized"]) == {"optimization", "evaluation"},
        "both phase owners",
    )
    for bound in first["materialized"].values():
        require(
            set(bound) == {"manifest", "owner", "registry", "feedback", "source"},
            "owner schema",
        )
        for key, value in bound.items():
            if key == "source" and value is None:
                continue
            require(
                type(value) is str
                and len(value) == 64
                and all(c in "0123456789abcdef" for c in value),
                "owner digest",
            )
    for key in ("configuration", "binding", "candidate", "persisted", "report"):
        value = result[key]
        if key == "report" and cancelled and value is None:
            continue
        require(
            type(value) is str
            and len(value) == 64
            and all(c in "0123456789abcdef" for c in value),
            "outcome digest",
        )
    require(result["state"] == ("compiled" if cancelled else "complete"), "claim state")
    require(result["status"] == (None if cancelled else "blocked"), "blocked outcome")
    require((result["report"] is None) == cancelled, "stored result kind")
    require(second["new_dispatches"] == (0 if cancelled else 1), "dispatch count")
    require(
        second["predict_entries"]
        == (0 if cancelled else 2 if case == "calls_exhausted" else 1),
        "actual prediction entries",
    )
    require(second["stages"] == ([] if cancelled else ["baseline"]), "dispatch phase")
    require(
        second["forbidden_framework"] == 0 and second["retry_preserved"] is True,
        "retry/framework denial",
    )
    require(
        third["new_dispatches"] == third["framework_imports"] == 0,
        "fresh replay effects",
    )
    require(
        all(
            type(p["network_attempts"]) is int and p["network_attempts"] == 0
            for p in (first, second, third)
        ),
        "network attempted",
    )


def child(phase: str, path: Path, case: str) -> dict[str, Any]:
    forbidden: list[str] = []

    def deny(*_: Any, **__: Any) -> Any:
        forbidden.append("network")
        raise ValueError("network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
        contextlib.redirect_stdout(BoundedLog()),
        contextlib.redirect_stderr(BoundedLog()),
    ):
        if phase == "compile":
            result = compile_child(path, forbidden, case)
        else:
            raw = (
                path / ("compile.json" if phase == "evaluate" else "evaluate.json")
            ).read_bytes()
            require(len(raw) <= 65536, "prior receipt bound")
            result = (evaluate_child if phase == "evaluate" else replay_child)(
                path, json.loads(raw), forbidden, case
            )
        require(not forbidden, "network attempted")
        return result


def source_hashes() -> dict[str, str]:
    from refinery import gepa_runtime as runtime

    return {
        **h.source_hashes(),
        "tests/test_gepa_heldout_adversarial_harness.py": runtime.digest(
            runtime.source_bytes("tests/test_gepa_heldout_adversarial_harness.py")
        ),
        "tools/test_gepa_heldout_adversarial.py": runtime.digest(
            runtime.source_bytes("tools/test_gepa_heldout_adversarial.py")
        ),
    }


def validate_report(report: dict[str, Any]) -> None:
    from refinery import gepa_runtime as runtime
    from tools.offline_gepa import GIT_SHA256

    require(
        report["schema"] == "runspindle.gepa313-heldout-adversarial.v1"
        and report["status"] == "passed",
        "schema/status",
    )
    require(
        report["sources"] == source_hashes()
        and report["adoption_sha256"] == runtime.ADOPTION_SHA256,
        "source binding",
    )
    raw = runtime.source_bytes("dependencies/tether-feedback.json")
    pin = json.loads(raw)
    require(
        report["inputs"]
        == dict(
            git_sha256=GIT_SHA256,
            tether_commit=pin["commit"],
            tether_pin_sha256=runtime.digest(raw),
            tether_files=pin["files"],
        ),
        "input pins",
    )
    require(report["preparation_network_attempts"] == 0, "preparation network")
    validate_phases(
        report["case"], report["compile"], report["evaluate"], report["replay"]
    )


def run(case: str) -> dict[str, Any]:
    from refinery import gepa_runtime as runtime
    from tools.offline_gepa import bounded, materialized_inputs

    require(case in CASES, "unknown case")
    inputs = materialized_inputs("/tether", "/usr/bin/git")
    sources = source_hashes()
    forbidden: list[str] = []

    def deny(*_: Any, **__: Any) -> Any:
        forbidden.append("network")
        raise ValueError("preparation network")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
        tempfile.TemporaryDirectory(prefix="gepa-adversarial-") as tmp,
        h.signed_fixture(Path(tmp)),
    ):
        path = Path(tmp)
        phases = {}
        env = dict(
            PATH="/usr/local/bin:/usr/bin:/bin",
            PYTHONPATH="/deps:/work",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONHASHSEED="0",
            HOME=tmp,
            DSPY_CACHEDIR=str(path / "cache"),
            HF_HUB_OFFLINE="1",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
            LC_ALL="C",
            TZ="UTC",
            RUNSPINDLE_TETHER_SOURCE="/tether",
        )
        for phase in ("compile", "evaluate", "replay"):
            response = bounded(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child",
                    phase,
                    tmp,
                    case,
                ],
                CHILD_SECONDS,
                env=env,
            )
            require(
                response.returncode == 0,
                "adversarial child failed: " + response.stderr[-4096:],
            )
            phases[phase] = json.loads(response.stdout)
            with (path / (phase + ".json")).open("x") as out:
                json.dump(phases[phase], out, sort_keys=True)
        validate_phases(case, phases["compile"], phases["evaluate"], phases["replay"])
    require(sources == source_hashes() and not forbidden, "source/network changed")
    report = dict(
        schema="runspindle.gepa313-heldout-adversarial.v1",
        status="passed",
        case=case,
        inputs=inputs,
        sources=sources,
        adoption_sha256=runtime.ADOPTION_SHA256,
        preparation_network_attempts=0,
        **phases,
        scope="Synthetic signed mixed input; actual GEPA compile and heldout accounting; cancelled claim replay differs from completed blocked report replay; no live provider or promotion; physical owner hashes retained.",
    )
    validate_report(report)
    return report


if __name__ == "__main__":
    if (
        len(sys.argv) == 5
        and sys.argv[1] == "--child"
        and sys.argv[2] in ("compile", "evaluate", "replay")
    ):
        print(
            json.dumps(
                child(sys.argv[2], Path(sys.argv[3]), sys.argv[4]), sort_keys=True
            )
        )
    elif len(sys.argv) == 3 and sys.argv[1] == "--case":
        print(json.dumps(run(sys.argv[2]), sort_keys=True))
    else:
        raise ValueError("invalid adversarial harness arguments")
