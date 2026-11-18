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

CHILD_SECONDS = 30  # Three sequential children within the unchanged 120s outer cap.
REFERENCE = "explicit-test-grant"
TETHER = Path("/tether")


@contextmanager
def signed_fixture(path: Path) -> Any:
    require(
        os.environ.get("RUNSPINDLE_TETHER_SOURCE") == str(TETHER),
        "fixed Tether mount required",
    )
    fixture: Any = importlib.import_module(
        "test_materialized_durable"
    ).MaterializedDurableTests()
    try:
        fixture.setUp()
        batches = fixture.mixed_batches()
        source = fixture.source
        metadata = dict(
            store=str(source.store.path),
            feedback=source.use.tether_store.db.execute(
                "PRAGMA database_list"
            ).fetchone()[2],
            registry=str(fixture.h.registry.path),
            snapshot=source.snapshot,
        )
        (path / "materialized.json").write_text(json.dumps(metadata, sort_keys=True))
        (path / "manifests.json").write_text(
            json.dumps([b.manifest.decode() for b in batches])
        )
        (path / "selections.json").write_text(
            json.dumps(
                {
                    b.selections[0].item_id: vars(b.selections[0].source.selection)
                    for b in batches
                },
                sort_keys=True,
            )
        )
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
    manifests = json.loads(bounded_file(path / "manifests.json", 524288))
    require(type(manifests) is list and len(manifests) == 2, "two phase manifests")
    require(
        all(type(m) is str and len(m.encode()) <= 262144 for m in manifests),
        "manifest bounds",
    )
    manifests = tuple(m.encode() for m in manifests)
    manifest = manifests[0]
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
        require(len(receipt["selected"]) == 2, "exact two-example fixture required")
        rows = receipt["selected"]
        pairs = tuple(
            (
                r["target"],
                Material(value["batch"][0][i], value["batch"][1][i], r["material_ref"]),
            )
            for i, r in enumerate(rows)
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
                "evaluation",
                "dataset_materialization",
                "feedback_projection",
            }

        use = RetainedFeedbackUse(
            dep,
            feedback,
            verify_material=lambda target, material: any(
                target == t and material == m for t, m in pairs
            ),
            approve_use=approve_use,
        )
        sources = tuple(
            RetainedSource(
                store,
                snapshot,
                SelectedFeedback(**r["selection"]),
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
            for r in rows
        )

        def approve(descriptor: Any, purpose: str, reference: str) -> bool:
            require(len(grants) < 4096, "bounded authority observations")
            grants.append(purpose)
            return (
                descriptor.get("item_id") in items
                and reference == REFERENCE
                and purpose
                in {
                    "optimization",
                    "reflection",
                    "evaluation",
                    "withdraw",
                }
            )

        items = tuple(json.loads(m)["items"][0]["item"]["item_id"] for m in manifests)
        batches = []
        selected = json.loads(bounded_file(path / "selections.json", 32768))
        require(
            type(selected) is dict and set(selected) == set(items),
            "exact selected mapping",
        )
        for manifest, item in zip(manifests, items):
            matching = [
                source for source in sources if vars(source.selection) == selected[item]
            ]
            require(len(matching) == 1, "exact materialized source mapping")
            batches.append(
                AuthorizedBatch(
                    manifest,
                    registry,
                    (Selection(item, matching[0]),),
                    approve,
                    REFERENCE,
                )
            )
        yield dict(
            store=store,
            snapshot=snapshot,
            use=use,
            batches=tuple(batches),
            registry=registry,
            approve=approve,
            items=items,
            grants=grants,
            retained_grants=retained_grants,
        )


def authority_receipt(
    material: dict[str, Any], purposes: tuple[str, ...]
) -> dict[str, bool]:
    return {
        p: p in material["grants"] and p in material["retained_grants"]
        for p in purposes
    }


def materialized_identity(session: Any, material: dict[str, Any]) -> dict[str, Any]:
    from refinery.lineage import digest

    return {
        b.purpose: dict(
            manifest=digest(b.manifest),
            owner=b.identity,
            registry=b.storage_binding(b.registry),
            feedback=b.storage_binding(b.feedback),
            source=None if b.source is None else b.storage_binding(b.source),
        )
        for b in session.batches()
    }


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
        session, admission = admitted_session(material, intent="start")
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
            "admission": admission,
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


def session_for(material: dict[str, Any], *, intent: str) -> Any:
    from refinery import evaluation as ev
    from refinery.durable_dspy import Session
    from refinery.optimization import validate_dataset
    from refinery.storage import VERSION

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
    return Session.materialized_learning(
        material["store"],
        VERSION,
        "materialized-gepa-heldout",
        material["snapshot"],
        provider=ev.ScriptedProvider(
            tuple(ev.Completion(v, 1, 1, True, 0) for v in script),
            clock=ev.VirtualClock(),
        ),
        limits=ev.Limits(16, 100, 1000, 100),
        per_call=ev.Reservation(10, 10),
        budget_id="materialized-gepa-heldout",
        intent=intent,
        training_output_id=train[0].output.output_id,
        retained_use=material["use"],
        materialized_batches=material["batches"],
    )


def admitted_session(
    material: dict[str, Any], *, intent: str
) -> tuple[Any, dict[str, Any]]:
    """Observe the original factory; reject omission, effects and checkpoint writes."""
    from refinery import evaluation as ev
    from refinery.durable_dspy import Session

    before = persisted(material["store"])
    original = Session.materialized_learning
    require(
        original.__module__ == "refinery.durable_dspy"
        and original.__qualname__ == "Session.materialized_learning"
        and Path(original.__code__.co_filename).resolve()
        == ROOT / "refinery/durable_dspy.py",
        "original factory implementation replaced",
    )
    entries: list[tuple[str, Any]] = []
    provider_entries: list[bool] = []
    framework_entries: list[bool] = []

    def factory(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        entries.append((cast(str, kwargs.get("intent")), result))
        return result

    def deny_provider(*_: Any, **__: Any) -> Any:
        provider_entries.append(True)
        raise ValueError("admission provider execution forbidden")

    def deny_framework(*_: Any, **__: Any) -> Any:
        framework_entries.append(True)
        raise ValueError("admission framework execution forbidden")

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(Session, "materialized_learning", staticmethod(factory))
        )
        stack.enter_context(
            patch.object(ev.ScriptedProvider, "complete", deny_provider)
        )
        # Never import a framework merely to observe admission, notably replay.
        dspy = sys.modules.get("dspy")
        if dspy is not None:
            stack.enter_context(patch.object(dspy.GEPA, "compile", deny_framework))
            stack.enter_context(patch.object(dspy.Predict, "forward", deny_framework))
        session = session_for(material, intent=intent)
    after = persisted(material["store"])
    require(
        type(session) is Session
        and len(entries) == 1
        and entries[0][0] == intent
        and entries[0][1] is session,
        "original admission factory bypassed",
    )
    require(
        not provider_entries and not framework_entries and not session.provider.calls,
        "admission attempted execution",
    )
    require(before == after, "admission changed checkpoint")
    return session, dict(
        intent=intent,
        factory_entries=len(entries),
        provider_entries=len(provider_entries),
        framework_entries=len(framework_entries),
        cursor_entries=len(session.provider.calls),
        before=before,
        after=after,
    )


def validate_admission(value: Any, *, intent: str, checkpoint: str) -> None:
    require(
        type(value) is dict
        and set(value)
        == {
            "intent",
            "factory_entries",
            "provider_entries",
            "framework_entries",
            "cursor_entries",
            "before",
            "after",
        },
        "admission receipt schema",
    )
    require(
        value["intent"] == intent
        and type(value["factory_entries"]) is int
        and value["factory_entries"] == 1,
        "explicit original factory required",
    )
    require(
        all(
            type(value[k]) is int and value[k] == 0
            for k in ("provider_entries", "framework_entries", "cursor_entries")
        ),
        "admission execution",
    )
    require(
        value["before"] == value["after"] == checkpoint, "admission checkpoint changed"
    )
    require(
        type(checkpoint) is str
        and len(checkpoint) == 64
        and all(c in "0123456789abcdef" for c in checkpoint),
        "admission checkpoint digest",
    )


def evaluated_identity(session: Any, report: dict[str, Any]) -> dict[str, Any]:
    from refinery.lineage import digest
    from refinery.storage import pack

    return dict(
        candidate=report["candidate_sha256"],
        configuration=session.config,
        binding=session.binding_sha256,
        report=digest(pack(report)),
        usage=report["usage"],
        attempts=digest(pack(report["attempts"])),
        clock_ms=session.clock(),
        state=session.row(session.store.db)[1],
    )


def train_denied(session: Any) -> bool:
    from refinery import evaluation as ev

    try:
        session.guard("optimization")
    except (ValueError, ev.Halt):
        return True
    return False


def evaluate_child(
    path: Path, first: dict[str, Any], forbidden: list[str]
) -> dict[str, Any]:
    from refinery import evaluation as ev
    from refinery import gepa_runtime

    dspy: Any = importlib.import_module("dspy")
    original_predict = dspy.Predict.forward
    original_complete = ev.ScriptedProvider.complete
    observed: list[tuple[Any, str | None]] = []
    compiler: list[bool] = []
    reflection: list[bool] = []
    predict_calls: list[bool] = []

    def deny_compile(*_: Any, **__: Any) -> Any:
        compiler.append(True)
        raise ValueError("heldout compiler forbidden")

    def predict(instance: Any, *args: Any, **kwargs: Any) -> Any:
        if set(instance.signature.input_fields) == {"evidence"}:
            reflection.append(True)
            raise ValueError("heldout reflection forbidden")
        predict_calls.append(True)
        return original_predict(instance, *args, **kwargs)

    def complete(instance: Any, request: Any) -> Any:
        return observe_completion(original_complete, observed, instance, request)

    with materialized_context(path) as material:
        session, admission = admitted_session(material, intent="resume_evaluation")
        require(
            persisted(session.store) == first["persisted"],
            "checkpoint before evaluation changed",
        )
        require(
            materialized_identity(session, material) == first["materialized"],
            "phase owner changed",
        )
        require(not session.provider.calls, "fresh evaluation cursor required")
        with (
            patch.object(dspy.GEPA, "compile", deny_compile),
            patch.object(dspy.Predict, "forward", predict),
            patch.object(ev.ScriptedProvider, "complete", complete),
        ):
            report = session.evaluate()
        require(report["status"] == "evaluated", "heldout evaluation failed")
        require(
            not compiler and not reflection and len(predict_calls) == 2,
            "heldout framework path changed",
        )
        require(
            len(observed) == 2
            and [r.stage for r, _ in observed] == ["baseline", "candidate"],
            "heldout dispatch count/order",
        )
        require(
            observed == [(a.request, a.content_sha256) for a in report["attempts"]],
            "heldout receipt mismatch",
        )
        require(
            report["scores"] == {"baseline": 0, "candidate": 1},
            "heldout synthetic score",
        )
        require(
            report["usage"]["calls"] == 7 and report["usage"]["unresolved_calls"] == 0,
            "shared budget changed",
        )
        require(len(session.provider.calls) == 7, "compile cursor not restored")
        current = persisted(session.store)
        require(train_denied(session), "training reentered sealed cohort")
        require(
            current == persisted(session.store), "training denial changed checkpoint"
        )
        return dict(
            phase="evaluate",
            admission=admission,
            pid=os.getpid(),
            identity=evaluated_identity(session, report),
            persisted=current,
            materialized=materialized_identity(session, material),
            authorization=authority_receipt(material, ("evaluation",)),
            new_dispatches=len(observed),
            predict_entries=len(predict_calls),
            compile_entries=len(compiler),
            reflection_entries=len(reflection),
            stages=[r.stage for r, _ in observed],
            scores=report["scores"],
            observations_match_receipts=True,
            candidate_matches_compile=report["candidate_sha256"]
            == first["identity"]["candidate"],
            training_after_seal_denied=True,
            network_attempts=len(forbidden),
            runtime_identity=gepa_runtime.runtime_identity(),
        )


class RejectFramework(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname.split(".")[0] in {"dspy", "gepa"}:
            self.attempts.append(fullname)
            raise ValueError("framework import forbidden during replay")
        return None


def replay_child(
    path: Path, second: dict[str, Any], forbidden: list[str]
) -> dict[str, Any]:
    require(
        not any(n.split(".")[0] in {"dspy", "gepa"} for n in sys.modules),
        "fresh interpreter required",
    )
    reject = RejectFramework()
    calls: list[bool] = []

    def deny(*_: Any, **__: Any) -> Any:
        calls.append(True)
        raise ValueError("replay effect forbidden")

    sys.meta_path.insert(0, reject)
    try:
        from refinery import evaluation as ev

        with (
            patch.object(ev.ScriptedProvider, "complete", deny),
            materialized_context(path) as material,
        ):
            session, admission = admitted_session(material, intent="resume_evaluation")
            require(
                persisted(session.store) == second["persisted"],
                "evaluation checkpoint changed",
            )
            report = session.evaluate()
            ident = evaluated_identity(session, report)
            current = persisted(session.store)
            require(
                current == second["persisted"] and ident == second["identity"],
                "replay identity changed",
            )
            require(train_denied(session), "replay reopened training")
            require(
                not calls and not reject.attempts and not session.provider.calls,
                "replay attempted effects/import",
            )
            authorizations = authority_receipt(material, ("evaluation",))
            materialized = materialized_identity(session, material)
            batch = next(
                b
                for b in material["batches"]
                if json.loads(b.manifest)["purpose"] == "evaluation"
            )
            material["registry"].withdraw(
                batch.selections[0].item_id,
                approve=material["approve"],
                permission_ref=REFERENCE,
            )
            denied = False
            try:
                session.evaluate()
            except (ValueError, ev.Halt):
                denied = True
            require(
                denied and persisted(session.store) == current,
                "withdrawal replay escaped or mutated checkpoint",
            )
            require(
                not calls and not reject.attempts, "withdrawal attempted effects/import"
            )
            return dict(
                phase="replay",
                admission=admission,
                pid=os.getpid(),
                identity=ident,
                persisted=current,
                materialized=materialized,
                authorization=authorizations,
                withdrawal_denied=denied,
                withdrawal_preserved_checkpoint=True,
                training_after_seal_denied=True,
                new_dispatches=len(calls),
                framework_imports=len(reject.attempts),
                network_attempts=len(forbidden),
            )
    finally:
        sys.meta_path.remove(reject)


def validate_identity(value: Any, *, compiled: bool) -> None:
    hashes = (
        {
            "snapshot",
            "configuration",
            "binding",
            "candidate",
            "result",
            "attempts",
            "witnesses",
        }
        if compiled
        else {"candidate", "configuration", "binding", "report", "attempts"}
    )
    keys = hashes | {"usage", "clock_ms"} | (set() if compiled else {"state"})
    require(type(value) is dict and set(value) == keys, "exact phase identity schema")
    for key in hashes:
        digest_value = value[key]
        require(
            type(digest_value) is str
            and len(digest_value) == 64
            and all(c in "0123456789abcdef" for c in digest_value),
            "phase identity digest",
        )
    require(
        type(value["clock_ms"]) is int and value["clock_ms"] == 0,
        "synthetic clock identity",
    )
    usage = value["usage"]
    require(
        type(usage) is dict
        and set(usage)
        == {
            "calls",
            "tokens",
            "cost_microunits",
            "elapsed_ms",
            "unresolved_calls",
            "reason",
        },
        "exact usage identity",
    )
    total = 5 if compiled else 7
    require(
        all(
            type(usage[k]) is int and usage[k] == total
            for k in ("calls", "tokens", "cost_microunits")
        ),
        "phase budget identity",
    )
    require(
        type(usage["elapsed_ms"]) is int
        and usage["elapsed_ms"] == 0
        and type(usage["unresolved_calls"]) is int
        and usage["unresolved_calls"] == 0
        and usage["reason"] is None,
        "resolved phase usage",
    )
    if not compiled:
        require(value["state"] == "complete", "complete evaluation required")


def validate_phases(
    first: dict[str, Any], second: dict[str, Any], third: dict[str, Any]
) -> None:
    validate(first["framework"])
    validate_admission(
        first["admission"], intent="start", checkpoint=first["admission"]["before"]
    )
    validate_admission(
        second["admission"], intent="resume_evaluation", checkpoint=first["persisted"]
    )
    validate_admission(
        third["admission"], intent="resume_evaluation", checkpoint=second["persisted"]
    )
    validate_identity(first["identity"], compiled=True)
    validate_identity(second["identity"], compiled=False)
    validate_identity(third["identity"], compiled=False)
    require(
        first["identity"]["binding"] == second["identity"]["binding"],
        "Session owner changed between phases",
    )
    require(
        first["identity"]["candidate"] == first["framework"]["candidate_sha256"]
        and first["identity"]["configuration"]
        == first["framework"]["configuration_sha256"],
        "compile checkpoint association changed",
    )
    for phase in (first, second, third):
        value = phase["persisted"]
        require(
            type(value) is str
            and len(value) == 64
            and all(c in "0123456789abcdef" for c in value),
            "checkpoint digest shape",
        )

    require(
        [v.get("phase") for v in (first, second, third)]
        == ["compile", "evaluate", "replay"],
        "exact fresh phases",
    )
    pids = [v.get("pid") for v in (first, second, third)]
    require(
        all(type(p) is int and p > 0 for p in pids) and len(set(pids)) == 3,
        "three distinct processes",
    )
    require(
        first["materialized"] == second["materialized"] == third["materialized"],
        "materialized identity changed",
    )
    require(
        set(first["materialized"]) == {"optimization", "evaluation"},
        "both manifests required",
    )
    for phase in (first, second, third):
        for bound in phase["materialized"].values():
            require(
                set(bound) == {"manifest", "owner", "registry", "feedback", "source"},
                "materialized identity schema",
            )
            for key, value in bound.items():
                if key == "source" and value is None:
                    continue
                require(
                    type(value) is str
                    and len(value) == 64
                    and all(c in "0123456789abcdef" for c in value),
                    "materialized digest shape",
                )

    require(
        first["authorization"] == {"optimization": True, "reflection": True},
        "compile grants missing",
    )
    for phase in (second, third):
        require(
            phase["authorization"] == {"evaluation": True}, "evaluation grant missing"
        )
        require(phase["training_after_seal_denied"] is True, "sealed training allowed")
    require(
        second["new_dispatches"] == second["predict_entries"] == 2
        and second["compile_entries"] == second["reflection_entries"] == 0,
        "heldout effects changed",
    )
    require(
        second["stages"] == ["baseline", "candidate"]
        and second["scores"] == {"baseline": 0, "candidate": 1},
        "heldout result changed",
    )
    require(
        second["observations_match_receipts"] is True
        and second["candidate_matches_compile"] is True,
        "heldout association changed",
    )
    require(
        second["identity"]["candidate"] == first["identity"]["candidate"]
        and second["identity"]["configuration"] == first["identity"]["configuration"],
        "candidate changed",
    )
    require(
        second["identity"] == third["identity"]
        and second["persisted"] == third["persisted"],
        "replay checkpoint changed",
    )
    usage = second["identity"]["usage"]
    require(
        second["identity"]["state"] == "complete"
        and usage["calls"] == usage["tokens"] == usage["cost_microunits"] == 7
        and usage["unresolved_calls"] == 0,
        "settled shared budget required",
    )
    require(
        third["withdrawal_denied"] is True
        and third["withdrawal_preserved_checkpoint"] is True,
        "withdrawal denied required",
    )
    require(
        third["new_dispatches"] == third["framework_imports"] == 0, "replay effects"
    )
    require(
        all(
            type(v["network_attempts"]) is int and v["network_attempts"] == 0
            for v in (first, second, third)
        ),
        "network attempt",
    )


def child(phase: str, path: Path) -> dict[str, Any]:
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
            result = compile_child(path, forbidden)
        else:
            name = "compile" if phase == "evaluate" else "evaluate"
            raw = (path / (name + ".json")).read_bytes()
            require(len(raw) <= 65536, "bounded prior receipt")
            result = (
                evaluate_child(path, json.loads(raw), forbidden)
                if phase == "evaluate"
                else replay_child(path, json.loads(raw), forbidden)
            )
        require(not forbidden, "network attempted")
        return result


def run_child(phase: str, path: Path) -> dict[str, Any]:
    from tools.offline_gepa import bounded

    env = dict(
        PATH="/usr/local/bin:/usr/bin:/bin",
        PYTHONPATH="/deps:/work",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONHASHSEED="0",
        HOME=str(path),
        DSPY_CACHEDIR=str(path / "cache"),
        HF_HUB_OFFLINE="1",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        LC_ALL="C",
        TZ="UTC",
        RUNSPINDLE_TETHER_SOURCE="/tether",
    )
    r = bounded(
        [sys.executable, str(Path(__file__).resolve()), "--child", phase, str(path)],
        CHILD_SECONDS,
        env=env,
    )
    require(r.returncode == 0, "heldout child failed: " + r.stderr[-4096:])
    v = json.loads(r.stdout)
    require(type(v) is dict, "invalid receipt")
    return cast(dict[str, Any], v)


def source_hashes() -> dict[str, str]:
    from refinery import gepa_runtime as runtime
    from tools.test_gepa_materialized import source_hashes as materialized_sources

    return {
        **materialized_sources(),
        "tests/test_learning_admission_harness.py": runtime.digest(
            runtime.source_bytes("tests/test_learning_admission_harness.py")
        ),
        "tests/test_learning_admission.py": runtime.digest(
            runtime.source_bytes("tests/test_learning_admission.py")
        ),
        "tools/test_gepa_heldout_materialized.py": runtime.digest(
            runtime.source_bytes("tools/test_gepa_heldout_materialized.py")
        ),
    }


def validate_report(report: dict[str, Any]) -> None:
    from refinery import gepa_runtime as runtime
    from tools.offline_gepa import GIT_SHA256

    require(
        report.get("schema") == "runspindle.gepa313-materialized-heldout.v1"
        and report.get("status") == "passed",
        "report schema/status",
    )
    require(
        report["sources"] == source_hashes()
        and report["adoption_sha256"] == runtime.ADOPTION_SHA256,
        "source binding",
    )
    pin_raw = runtime.source_bytes("dependencies/tether-feedback.json")
    pin = json.loads(pin_raw)
    require(
        report["inputs"]
        == dict(
            git_sha256=GIT_SHA256,
            tether_commit=pin["commit"],
            tether_pin_sha256=runtime.digest(pin_raw),
            tether_files=pin["files"],
        ),
        "input pins",
    )
    require(
        type(report["preparation_network_attempts"]) is int
        and report["preparation_network_attempts"] == 0,
        "preparation network",
    )
    validate_phases(report["compile"], report["evaluate"], report["replay"])


def run() -> dict[str, Any]:
    from refinery import gepa_runtime as runtime
    from tools.offline_gepa import materialized_inputs

    inputs = materialized_inputs(str(TETHER), "/usr/bin/git")
    sources = source_hashes()
    forbidden: list[str] = []

    def deny(*_: Any, **__: Any) -> Any:
        forbidden.append("network")
        raise ValueError("preparation network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
        tempfile.TemporaryDirectory(prefix="gepa-heldout-") as tmp,
        signed_fixture(Path(tmp)),
    ):
        path = Path(tmp)
        phases = {}
        for phase in ("compile", "evaluate", "replay"):
            phases[phase] = run_child(phase, path)
            with (path / (phase + ".json")).open("x") as out:
                json.dump(phases[phase], out, sort_keys=True)
        validate_phases(phases["compile"], phases["evaluate"], phases["replay"])
    require(sources == source_hashes() and not forbidden, "source/network changed")
    report = dict(
        schema="runspindle.gepa313-materialized-heldout.v1",
        status="passed",
        inputs=inputs,
        sources=sources,
        adoption_sha256=runtime.ADOPTION_SHA256,
        preparation_network_attempts=len(forbidden),
        **phases,
        scope="synthetic mixed materialized GEPA compile/evaluate/replay; actual current authority fixtures, no live provider or deployment; physical owner hashes retained without cross-run equality",
    )
    validate_report(report)
    return report


if __name__ == "__main__":
    if (
        len(sys.argv) == 4
        and sys.argv[1] == "--child"
        and sys.argv[2] in {"compile", "evaluate", "replay"}
    ):
        print(json.dumps(child(sys.argv[2], Path(sys.argv[3])), sort_keys=True))
    elif len(sys.argv) == 1:
        print(json.dumps(run(), sort_keys=True))
    else:
        raise ValueError("invalid heldout harness arguments")
