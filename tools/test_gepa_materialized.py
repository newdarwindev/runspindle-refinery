"""Actual signed materialized training, GEPA compilation and same-file recovery.

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
from contextlib import ExitStack, contextmanager
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


REFERENCE = "explicit-test-grant"
TETHER = Path("/tether")


@contextmanager
def signed_fixture(path: Path) -> Any:
    """Keep actual signed local stores alive across children; never clone files."""
    require(
        os.environ.get("RUNSPINDLE_TETHER_SOURCE") == str(TETHER),
        "fixed Tether mount required",
    )
    fixture: Any = importlib.import_module(
        "test_materialized_durable"
    ).MaterializedDurableTests()
    try:
        fixture.setUp()
        source = fixture.source
        metadata = {
            "store": str(source.store.path),
            "feedback": source.use.tether_store.db.execute(
                "PRAGMA database_list"
            ).fetchone()[2],
            "registry": str(fixture.h.registry.path),
            "snapshot": source.snapshot,
        }
        (path / "materialized.json").write_text(json.dumps(metadata, sort_keys=True))
        (path / "manifest.json").write_bytes(fixture.batch.manifest)
        yield
    finally:
        fixture.doCleanups()


@contextmanager
def materialized_context(path: Path) -> Any:
    from refinery.dataset_batch import Selection
    from refinery.dataset_materialization import DatasetRegistry
    from refinery.feedback_collection import Material, SelectedFeedback
    from refinery.feedback_projection import RetainedSource
    from refinery.feedback_use import RetainedFeedbackUse
    from refinery.materialized_use import AuthorizedBatch
    from refinery.storage import Store
    from refinery.tether_dependency import TetherDependency, bounded_file

    raw = bounded_file(path / "materialized.json", 16384)
    require(len(raw) <= 16384, "materialized metadata bound")
    meta = json.loads(raw)
    require(
        type(meta) is dict
        and set(meta) == {"store", "feedback", "registry", "snapshot"},
        "metadata schema",
    )
    require(
        all(type(v) is str and len(v) <= 4096 for v in meta.values()),
        "metadata field bounds",
    )
    manifest = bounded_file(path / "manifest.json", 262144)
    require(len(manifest) <= 262144, "manifest bound")
    with ExitStack() as stack:
        store = stack.enter_context(Store(Path(meta["store"])))
        dep = TetherDependency(TETHER)
        feedback = dep.modules["tether.feedback"].FeedbackStore(meta["feedback"], None)
        stack.callback(feedback.close)
        registry = DatasetRegistry(Path(meta["registry"]))
        stack.callback(registry.close)
        snapshot = meta["snapshot"]
        value = store.get("lineage", snapshot)
        receipt = store.get("feedback_collection", value["collection_evidence"])
        require(len(receipt["selected"]) == 1, "exact single training fixture required")
        row = receipt["selected"][0]
        expected = Material(
            value["batch"][0][0], value["batch"][1][0], row["material_ref"]
        )
        grants: list[str] = []
        retained_grants: list[str] = []

        def approve_use(request: Any) -> bool:
            require(
                len(retained_grants) < 4096, "bounded retained authority observations"
            )
            retained_grants.append(request.purpose)
            return request.snapshot_sha256 == snapshot and request.purpose in {
                "optimization",
                "reflection",
                "dataset_materialization",
                "feedback_projection",
            }

        use = RetainedFeedbackUse(
            dep,
            feedback,
            verify_material=lambda target, material: target == row["target"]
            and material == expected,
            approve_use=approve_use,
        )
        source = RetainedSource(
            store,
            snapshot,
            SelectedFeedback(**row["selection"]),
            use,
            lambda key, destination: dict(
                revision="policy1",
                collection_allowed=True,
                use_allowed=True,
                export_allowed=True,
                current_target=True,
            )
            if destination == "local-dataset:synthetic"
            else {},
        )

        def approve(descriptor: Any, purpose: str, reference: str) -> bool:
            require(len(grants) < 4096, "bounded authority observations")
            grants.append(purpose)
            return (
                descriptor.get("item_id") == item
                and reference == REFERENCE
                and purpose
                in {
                    "optimization",
                    "reflection",
                    "withdraw",
                }
            )

        item = json.loads(manifest)["items"][0]["item"]["item_id"]
        batch = AuthorizedBatch(
            manifest, registry, (Selection(item, source),), approve, REFERENCE
        )
        yield dict(
            store=store,
            snapshot=snapshot,
            use=use,
            batch=batch,
            registry=registry,
            approve=approve,
            item_id=item,
            grants=grants,
            retained_grants=retained_grants,
        )


def authority_receipt(material: dict[str, Any]) -> dict[str, bool]:
    return {
        purpose: purpose in material["grants"]
        and purpose in material["retained_grants"]
        for purpose in ("optimization", "reflection")
    }


def materialized_identity(session: Any, material: dict[str, Any]) -> dict[str, Any]:
    from refinery.lineage import digest

    (bound,) = session.batches()
    return dict(
        manifest=digest(material["batch"].manifest),
        permission=digest(REFERENCE.encode()),
        owner=bound.identity,
        registry=bound.storage_binding(bound.registry),
        feedback=bound.storage_binding(bound.feedback),
        source=None if bound.source is None else bound.storage_binding(bound.source),
        tether=material["use"].dependency.pin["commit"],
    )


def persisted(store: Any) -> str:
    from refinery.lineage import digest
    from refinery.storage import pack

    # Logical exact stored bytes, not physical SQLite/WAL layout.
    tables = {}
    for table in (
        "objects",
        "families",
        "budgets",
        "experiments",
        "attempts",
        "environments",
        "activations",
    ):
        tables[table] = tuple(
            store.db.execute("SELECT * FROM " + table + " ORDER BY 1,2").fetchall()
        )
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
        "witnesses": digest(pack(tuple(witnesses))),
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
            "materialized-gepa",
            snapshot,
            provider=provider,
            limits=ev.Limits(16, 100, 1000, 100),
            per_call=ev.Reservation(10, 10),
            budget_id="materialized-gepa",
            strategy="gepa",
            training_output_id=train.output.output_id,
            retained_use=material["use"],
            materialized_batches=(material["batch"],),
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
            "materialized": materialized_identity(session, material),
            "authorization": authority_receipt(material),
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
        from refinery import evaluation as ev
        from refinery.durable_dspy import Session
        from refinery.storage import VERSION

        with (
            patch.object(ev.ScriptedProvider, "complete", deny_complete),
            materialized_context(path) as material,
        ):
            store = material["store"]
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
                "materialized-gepa",
                first["snapshot"],
                provider=provider,
                limits=ev.Limits(16, 100, 1000, 100),
                per_call=ev.Reservation(10, 10),
                budget_id="materialized-gepa",
                strategy="gepa",
                training_output_id=train.output.output_id,
                retained_use=material["use"],
                materialized_batches=(material["batch"],),
            )
            result = session.compile()
            require(
                not calls and not reject.attempts, "recovery attempted effects/import"
            )
            after = persisted(store)
            require(before == after, "recovery changed persisted checkpoint")
            recovered_identity = identity(session, result)
            materialized = materialized_identity(session, material)
            authorizations = authority_receipt(material)
            material["registry"].withdraw(
                material["item_id"],
                approve=material["approve"],
                permission_ref=REFERENCE,
            )
            denied = False
            try:
                session.recover_compiled()
            except (ValueError, ev.Halt):
                denied = True
            require(denied, "withdrawn materialized owner recovered")
            require(persisted(store) == after, "withdrawal denial changed checkpoint")
            require(
                not calls and not reject.attempts, "withdrawal attempted effect/import"
            )
            return {
                "pid": os.getpid(),
                "phase": "recover",
                "identity": recovered_identity,
                "materialized": materialized,
                "authorization": authorizations,
                "withdrawal_denied": denied,
                "withdrawal_preserved_checkpoint": persisted(store) == after,
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
        first["materialized"] == second["materialized"], "materialized owner changed"
    )
    for phase in (first, second):
        require(
            set(phase["materialized"])
            == {
                "manifest",
                "permission",
                "owner",
                "registry",
                "feedback",
                "source",
                "tether",
            },
            "materialized identity schema",
        )
        for key, value in phase["materialized"].items():
            if key == "tether" or (key == "source" and value is None):
                continue
            require(
                type(value) is str
                and len(value) == 64
                and all(c in "0123456789abcdef" for c in value),
                "materialized digest shape",
            )
        require(
            phase["materialized"]["tether"]
            == "1e8d2091c55dfad47fdc64ec2e73fcd946da9921",
            "wrong signed source pin",
        )
        require(
            phase["authorization"] == {"optimization": True, "reflection": True},
            "missing current purpose grant",
        )
    require(
        second["withdrawal_denied"] is True
        and second["withdrawal_preserved_checkpoint"] is True,
        "withdrawal fence missing",
    )
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
        "RUNSPINDLE_TETHER_SOURCE": "/tether",
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
        **{
            p: runtime.digest(runtime.source_bytes(p))
            for p in (
                "tools/test_gepa_materialized.py",
                "tools/test_gepa_durable.py",
                "tests/test_materialized_durable.py",
                "tests/test_dataset_materialization.py",
                "tests/test_feedback_projection.py",
                "tests/test_thumbs_up_collection.py",
                "tests/test_feedback_collection.py",
                "dependencies/tether-feedback.json",
            )
        },
    }


def validate_report(report: dict[str, Any]) -> None:
    from refinery import gepa_runtime as runtime

    require(
        report.get("schema") == "runspindle.gepa313-materialized-durable.v1"
        and report.get("status") == "passed",
        "durable report status",
    )
    require(
        report.get("sources") == source_hashes()
        and report.get("adoption_sha256") == runtime.ADOPTION_SHA256,
        "durable source binding",
    )
    require(
        type(report["preparation_network_attempts"]) is int
        and report["preparation_network_attempts"] == 0,
        "preparation network effect",
    )
    from tools.offline_gepa import GIT_SHA256

    pin_raw = runtime.source_bytes("dependencies/tether-feedback.json")
    pin = json.loads(pin_raw)
    require(
        report["inputs"]
        == {
            "git_sha256": GIT_SHA256,
            "tether_commit": pin["commit"],
            "tether_pin_sha256": runtime.digest(pin_raw),
            "tether_files": pin["files"],
        },
        "materialized input report changed",
    )
    validate_pair(report["compile"], report["recover"])


def run() -> dict[str, Any]:
    from refinery import gepa_runtime as runtime
    from tools.offline_gepa import materialized_inputs

    inputs = materialized_inputs(str(TETHER), "/usr/bin/git")
    sources = source_hashes()
    forbidden: list[str] = []

    def deny(*_: Any, **__: Any) -> Any:
        forbidden.append("network")
        raise ValueError("materialized preparation network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
        tempfile.TemporaryDirectory(prefix="gepa-materialized-") as temp,
        signed_fixture(Path(temp)),
    ):
        directory = Path(temp)
        first = run_child("compile", directory)
        with (directory / "compile.json").open("x") as receipt:
            json.dump(first, receipt, sort_keys=True)
        second = run_child("recover", directory)
        validate_pair(first, second)
    require(sources == source_hashes(), "durable source changed")
    require(not forbidden, "materialized preparation network attempt")
    report = {
        "schema": "runspindle.gepa313-materialized-durable.v1",
        "status": "passed",
        "inputs": inputs,
        "preparation_network_attempts": len(forbidden),
        "compile": first,
        "recover": second,
        "sources": sources,
        "adoption_sha256": runtime.ADOPTION_SHA256,
        "scope": "actual signed synthetic materialized train, GEPA compile, same-file recovery and withdrawal denial; no heldout or live-provider qualification; physical owner hashes intentionally not cross-run normalized",
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
