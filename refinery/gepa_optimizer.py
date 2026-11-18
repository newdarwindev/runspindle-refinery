"""Bounded genuine GEPA reflection and locked instruction evaluation.

Shared durable ownership is supplied by Session; direct adapter calls do not
create once-only authority. No live provider, promotion or framework fallback.
New heldout execution still requires independent actual-framework qualification.
"""

from __future__ import annotations

import importlib.metadata
import threading
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable

from . import evaluation as ev
from . import gepa_codec as codec
from .dspy_optimizer import Dispatch, canonical, case, framed, framework
from .durable_evaluation import SQLiteBudget
from .lineage import Dataset, Example, LockedSplit, _token, digest
from .optimization import validate_dataset


def runtime_identity() -> str:
    from .gepa_runtime import runtime_identity as verify

    return verify()


@dataclass(frozen=True)
class Result:
    status: str
    reason: str | None
    configuration_sha256: str
    candidate: bytes | None
    candidate_sha256: str | None
    attempts: tuple[ev.Attempt, ...]
    usage: tuple[tuple[str, Any], ...]
    evidence_class: str = "process_local_gepa_reflection_unqualified"


class Transport(Dispatch):
    """Sealed task/reflection association, sharing the established settlement path."""

    def __init__(
        self,
        example: Example,
        split: LockedSplit,
        *,
        config: str,
        provider: ev.ScriptedProvider,
        budget: ev.Budget,
        per_call: ev.Reservation,
        check_use: Callable[[], None],
    ) -> None:
        super().__init__(
            (example,),
            split,
            stage="optimization",
            artifact_sha256=config,
            provider=provider,
            budget=budget,
            per_call=per_call,
            check_use=check_use,
        )
        self._execution_lock = threading.Lock()
        self._execution_local = threading.local()
        self._overlap = threading.Event()
        self.example = example
        self.config = config
        self.training_case = case(example)
        self.instructions: list[str] = [codec.INITIAL]
        self.admission: ContextVar[tuple[str, str, str] | None] = ContextVar(
            "gepa_admission", default=None
        )
        self.trace: list[tuple[str, str, ev.Attempt]] = []
        self.proposals: list[tuple[str, str, ev.Attempt]] = []
        self.reflection_calls = 0
        self.total_calls = 0
        self.signature: Any = None
        self._association_binding = (config, self.training_case, split.sha256, example)

    @contextmanager
    def execution_scope(self) -> Any:
        """Admit the fixed serial profile without replacing DSPy's executor."""
        if getattr(self._execution_local, "active", False):
            yield
            return
        if not self._execution_lock.acquire(blocking=False):
            self._overlap.set()
            raise ev.Halt("gepa_concurrent_execution")
        self._execution_local.active = True
        try:
            scope = (
                self.budget.store.worker_context()
                if type(self.budget) is SQLiteBudget
                else nullcontext()
            )
            with scope:
                self.check_binding()
                yield
                self.check_binding()
        finally:
            self._execution_local.active = False
            self._execution_lock.release()

    def check_binding(self) -> None:
        super().check_binding()
        if self._overlap.is_set():
            self.budget.stop("gepa_concurrent_execution")
            reason = self.budget.snapshot().get("reason")
            if type(reason) is not str:
                reason = "gepa_concurrent_execution"
            try:
                _token(reason)
            except ValueError:
                reason = "gepa_concurrent_execution"
            raise ev.Halt(reason)
        if (
            (self.config, self.training_case, self.split.sha256, self.example)
            != self._association_binding
            or self.instructions != [codec.INITIAL] + [row[0] for row in self.proposals]
            or len(self.proposals) > 1
        ):
            self.budget.stop("gepa_binding_changed")
            reason = self.budget.snapshot().get("reason")
            if type(reason) is not str:
                reason = "gepa_binding_changed"
            try:
                _token(reason)
            except ValueError:
                reason = "gepa_binding_changed"
            raise ev.Halt(reason)

    def task(self, predict: Any, value: str) -> Any:
        if (
            value != self.training_case
            or predict.demos
            or self.signature is None
            or not predict.signature.equals(
                self.signature.with_instructions(predict.signature.instructions)
            )
            or predict.signature.instructions not in self.instructions
        ):
            self.budget.stop("forbidden_program")
            raise ev.Halt("forbidden_program")
        return self.invoke_sealed(
            predict, "optimization", predict.signature.instructions, value
        )

    def invoke_sealed(
        self, predict: Any, stage: str, instruction: str, payload: str
    ) -> Any:
        if self.admission.get() is not None:
            self.budget.stop("reentrant_dispatch")
            raise ev.Halt("reentrant_dispatch")
        if stage == "optimization":
            if payload != self.training_case or instruction not in self.instructions:
                raise ValueError("unadmitted task")
        elif stage == "reflection":
            # Only one retained actual training failure is eligible for proposal.
            self.verify_payload(payload)
            if instruction != codec.INITIAL or self.proposals:
                raise ValueError("unadmitted proposal")
        else:
            raise ValueError("unadmitted stage")
        token = self.admission.set((stage, instruction, payload))
        try:
            return predict(
                **{"case" if stage == "optimization" else "evidence": payload}
            )
        finally:
            self.admission.reset(token)

    def verify_payload(self, payload: str) -> None:
        matches = [
            a
            for parent, answer, a in self.trace
            if parent == codec.INITIAL
            and answer != self.example.gold.decode()
            and codec.proposal_payload(
                self.config,
                self.training_case,
                answer,
                "correct" if answer == self.example.gold.decode() else "incorrect",
                a,
            )
            == payload
        ]
        if len(matches) != 1:
            raise ValueError("unverified proposal payload")

    def reserve(self, request: ev.Request) -> Any:
        active = self.admission.get()
        if active is None:
            raise ev.Halt("unattributed_dispatch")
        stage, instruction, payload = active
        if type(self.budget) is SQLiteBudget and self.budget.experiment is not None:
            return self.budget.reserve_gepa(
                self.per_call,
                request=request,
                witness=(request, instruction, payload),
            )
        return super().reserve(request)

    def _forward(self, messages: Any) -> Any:
        active = self.admission.get()
        if active is None:
            self.budget.stop("unattributed_dispatch")
            raise ev.Halt("unattributed_dispatch")
        stage, instruction, payload = active
        if (
            self.total_calls >= 16
            or len(self.attempts) >= 16
            or (stage == "reflection" and self.reflection_calls >= 2)
        ):
            self.budget.stop("gepa_dispatch_bound")
            raise ev.Halt("gepa_dispatch_bound")
        if stage == "reflection":
            self.verify_payload(payload)
            self.reflection_calls += 1
        elif payload != self.training_case or instruction not in self.instructions:
            self.budget.stop("forbidden_partition")
            raise ev.Halt("forbidden_partition")
        self.total_calls += 1
        request = ev.Request(
            stage,
            self.example.output.output_id,
            self.example.output.revision,
            self.example.source.sha256,
            codec.association(self.config, stage, instruction, payload),
            self.split.sha256,
            self.total_calls,
        )
        reply = self._complete(request, messages)
        if reply.content is None:
            raise ev.Halt("malformed_reply")
        answer = codec.text(reply.content.decode())
        if stage == "optimization":
            self.trace.append((instruction, answer, self.attempts[-1]))
        return framed(reply, "answer" if stage == "optimization" else "instruction")

    def metric_feedback(
        self, value: Any, gold: Any, answer: Any, name: Any
    ) -> tuple[float, str]:
        self.guard()
        if (
            value != self.training_case
            or gold != self.example.gold.decode()
            or name not in {None, codec.COMPONENT}
            or not self.trace
            or not self.attempts
        ):
            raise ValueError("unattributable metric")
        _, observed, attempt = self.trace[-1]
        if (
            type(answer) is not str
            or answer != observed
            or attempt != self.attempts[-1]
            or attempt.request.stage != "optimization"
            or attempt.content_sha256 != digest(answer.encode())
        ):
            raise ValueError("metric does not match latest task receipt")
        correct = answer == gold
        return float(correct), "correct" if correct else "incorrect"

    def propose(
        self,
        predict: Any,
        *,
        candidate: Any,
        reflective_dataset: Any,
        components_to_update: Any,
    ) -> dict[str, str]:
        self.guard()
        if (
            candidate != {codec.COMPONENT: codec.INITIAL}
            or components_to_update != [codec.COMPONENT]
            or type(reflective_dataset) is not dict
            or set(reflective_dataset) != {codec.COMPONENT}
            or type(reflective_dataset[codec.COMPONENT]) is not list
            or len(reflective_dataset[codec.COMPONENT]) != 1
            or self.proposals
        ):
            raise ValueError("unadmitted reflection component")
        row = reflective_dataset[codec.COMPONENT][0]
        if (
            type(row) is not dict
            or set(row) != {"Inputs", "Generated Outputs", "Feedback"}
            or row["Inputs"] != {"case": self.training_case}
            or type(row["Generated Outputs"]) is not dict
            or set(row["Generated Outputs"]) != {"answer"}
        ):
            raise ValueError("unadmitted reflection trace")
        answer = codec.text(row["Generated Outputs"]["answer"])
        feedback = "correct" if answer == self.example.gold.decode() else "incorrect"
        if row["Feedback"] != feedback or feedback != "incorrect":
            raise ValueError("reflection needs verified failure")
        matches = [
            a
            for parent, observed, a in self.trace
            if parent == codec.INITIAL and observed == answer
        ]
        if not matches:
            raise ValueError("reflection output not observed")
        payload = codec.proposal_payload(
            self.config, self.training_case, answer, feedback, matches[-1]
        )
        before = len(self.attempts)
        response = self.invoke_sealed(predict, "reflection", codec.INITIAL, payload)
        instruction = codec.text(response.instruction)
        if (
            instruction == codec.INITIAL
            or len(self.attempts) != before + 1
            or self.attempts[-1].content_sha256 != digest(instruction.encode())
        ):
            raise ValueError("unattributable reflection output")
        self.guard()
        self.proposals.append((instruction, payload, self.attempts[-1]))
        self.instructions.append(instruction)
        return {codec.COMPONENT: instruction}


def binding(
    dataset: Dataset,
    split: LockedSplit,
    *,
    training_output_id: str,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
) -> tuple[Example, str]:
    # Reject unknown adapters before reading properties or invoking their code.
    if (
        type(provider) is not ev.ScriptedProvider
        or type(budget) not in {ev.Budget, SQLiteBudget}
        or type(per_call) is not ev.Reservation
    ):
        raise ValueError("explicit offline adapters required")
    if (
        type(provider.clock) is not ev.VirtualClock
        or budget.clock_ms is not provider.clock
        or type(budget.limits) is not ev.Limits
        or type(provider.calls) is not list
    ):
        raise ValueError("reservation or clock binding changed")
    ev.Reservation(per_call.tokens, per_call.cost_microunits)
    ev.Limits(
        budget.limits.calls,
        budget.limits.tokens,
        budget.limits.time_ms,
        budget.limits.cost_microunits,
    )
    train, _ = validate_dataset(dataset, split)
    selected = [e for e in train if e.output.output_id == training_output_id]
    if len(selected) != 1 or not 1 <= budget.limits.calls <= 16:
        raise ValueError("explicit pristine bounded training run required")
    e = selected[0]
    codec.text(e.gold.decode())
    codec.text(case(e), 32768)
    from .gepa_runtime import source_identity

    runtime = source_identity()
    config = digest(
        canonical(
            {
                "format": codec.VERSION,
                "dataset": split.dataset_sha256,
                "split": split.sha256,
                "training_output_id": training_output_id,
                "script": provider.sha256,
                "limits": vars(budget.limits),
                "per_call": vars(per_call),
                "runtime_closure": runtime,
                "initial": codec.INITIAL,
                "max_metric_calls": 6,
                "seed": 0,
                "max_proposals": 1,
                "reflection_calls": 2,
                "dispatch_calls": 16,
            }
        )
    )
    return e, config


def optimize(
    dataset: Dataset,
    split: LockedSplit,
    *,
    training_output_id: str,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
    check_use: Callable[[], None],
) -> Result:
    e, config = binding(
        dataset,
        split,
        training_output_id=training_output_id,
        provider=provider,
        budget=budget,
        per_call=per_call,
    )
    if provider.calls:
        raise ValueError("explicit pristine bounded training run required")
    runtime_identity()
    dispatch = Transport(
        e,
        split,
        config=config,
        provider=provider,
        budget=budget,
        per_call=per_call,
        check_use=check_use,
    )
    dispatch.guard()
    dspy: Any = framework()
    if importlib.metadata.version("gepa") != "0.0.22":
        raise ValueError("unqualified GEPA version")

    class LocalLM(dspy.BaseLM):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(
                model="synthetic/local", cache=False, max_tokens=4096, **kwargs
            )

        def forward(
            self, prompt: Any = None, messages: Any = None, **kwargs: Any
        ) -> Any:
            if prompt is not None:
                raise ev.Halt("unexpected_prompt")
            return dispatch.forward(messages)

        def copy(self, **kwargs: Any) -> Any:
            return LocalLM(**kwargs)

    class Program(dspy.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.predict = dspy.Predict("case -> answer")
            self.predict.signature = self.predict.signature.with_instructions(
                codec.INITIAL
            )

        def forward(self, case: str) -> Any:
            with dispatch.execution_scope():
                return dispatch.task(self.predict, case)

    student, lm = Program(), LocalLM()
    dispatch.signature = student.predict.signature
    reflection = dspy.Predict("evidence -> instruction")
    example = dspy.Example(case=case(e), answer=e.gold.decode()).with_inputs("case")

    def metric(
        gold: Any,
        pred: Any,
        trace: Any = None,
        pred_name: Any = None,
        pred_trace: Any = None,
    ) -> Any:
        with dispatch.execution_scope():
            score, feedback = dispatch.metric_feedback(
                gold.case, gold.answer, pred.answer, pred_name
            )
        return dspy.Prediction(score=score, feedback=feedback)

    def proposer(
        candidate: Any, reflective_dataset: Any, components_to_update: Any
    ) -> Any:
        with dispatch.execution_scope():
            return dispatch.propose(
                reflection,
                candidate=candidate,
                reflective_dataset=reflective_dataset,
                components_to_update=components_to_update,
            )

    def stop(state: Any) -> bool:
        with dispatch.execution_scope():
            dispatch.guard()
            return bool(dispatch.proposals)

    candidate, reason = None, None
    try:
        with dspy.context(
            lm=lm,
            adapter=dspy.ChatAdapter(use_native_function_calling=False),
            disable_history=True,
            callbacks=[],
        ):
            optimizer = dspy.GEPA(
                metric=metric,
                max_metric_calls=6,
                reflection_minibatch_size=1,
                candidate_selection_strategy="current_best",
                instruction_proposer=proposer,
                reflection_lm=None,
                use_merge=False,
                num_threads=1,
                log_dir=None,
                track_stats=False,
                use_wandb=False,
                seed=0,
                gepa_kwargs={"stop_callbacks": [stop]},
            )
            compiled = optimizer.compile(student, trainset=[example], valset=[example])
        dispatch.guard()
        if (
            len(compiled.predictors()) != 1
            or compiled.predict.demos
            or not compiled.predict.signature.equals(
                student.predict.signature.with_instructions(
                    compiled.predict.signature.instructions
                )
            )
            or len(dispatch.proposals) != 1
        ):
            raise ValueError("optimizer changed fixed program or omitted reflection")
        instruction, payload, attempt = dispatch.proposals[0]
        if compiled.predict.signature.instructions != instruction:
            raise ValueError("GEPA did not select reflected instruction")
        candidate = codec.encode(
            instruction,
            payload,
            attempt,
            dataset,
            split,
            config,
            tuple(dispatch.attempts),
        )
        ledger: Any = budget
        ledger.checked_snapshot()
    except ev.Halt as exc:
        reason, candidate = str(exc), None
    except Exception:
        budget.stop("gepa_optimizer_failure")
        reason, candidate = (
            str(budget.snapshot().get("reason") or "gepa_optimizer_failure"),
            None,
        )
    return Result(
        "compiled" if candidate is not None else "blocked",
        reason,
        config,
        candidate,
        digest(candidate) if candidate is not None else None,
        tuple(dispatch.attempts),
        tuple(sorted(budget.snapshot().items())),
    )


def heldout_program_guard(
    student: Any, predict: Any, signature: Any, check_use: Callable[[], None]
) -> None:
    """Recheck current authority and the fixed original program before effects."""
    check_use()
    if (
        student.predict is not predict
        or len(student.predictors()) != 1
        or predict.demos
        or not predict.signature.equals(signature)
    ):
        raise ev.Halt("heldout_program_changed")


def evaluate_locked(
    compiled: Any,
    *,
    dataset: Dataset,
    split: LockedSplit,
    training_output_id: str,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
    check_use: Callable[[], None],
) -> dict[str, Any]:
    """Evaluate a locked instruction, never call GEPA.compile or a proposer.

    Session supplies ownership, durable once-only claims and heldout sealing.
    This adapter preserves its original compile cursor and cumulative budget.
    """
    from .dspy_optimizer import program
    from .gepa_evidence import GEPACompiled

    _, config = binding(
        dataset,
        split,
        training_output_id=training_output_id,
        provider=provider,
        budget=budget,
        per_call=per_call,
    )
    if (
        type(compiled) is not GEPACompiled
        or compiled.status != "compiled"
        or compiled.reason is not None
        or compiled.candidate is None
        or compiled.candidate_sha256 is None
        or compiled.configuration_sha256 != config
        or compiled.evidence_class != "offline_gepa_compile_component"
    ):
        raise ValueError("locked GEPA compilation required")
    instruction = codec.decode(
        compiled.candidate,
        compiled.candidate_sha256,
        dataset,
        split,
        config,
        compiled.attempts,
    )
    prefix = tuple(a.request for a in compiled.attempts)
    if tuple(provider.calls) != prefix or not prefix:
        raise ValueError("GEPA evaluation requires exact compiled cursor")
    ledger: Any = budget
    if ledger.snapshot()["calls"] < len(prefix):
        raise ValueError("GEPA shared ledger lost compile reservations")
    _, heldout = validate_dataset(dataset, split)
    if not heldout:
        raise ValueError("empty heldout")
    # Explicit runtime admission before imports; no new Python/dependency lane.
    runtime_identity()
    dspy = framework()
    scores: dict[str, int] = {}
    attempts: list[ev.Attempt] = []
    predictions: list[tuple[str, str, str, str]] = []
    try:
        for stage, text in (("baseline", codec.INITIAL), ("candidate", instruction)):
            artifact = (
                codec.baseline_artifact()
                if stage == "baseline"
                else compiled.candidate_sha256
            )
            student: Any = None
            predict: Any = None
            signature: Any = None

            def guard() -> None:
                heldout_program_guard(student, predict, signature, check_use)

            dispatch = Dispatch(
                heldout,
                split,
                stage=stage,
                artifact_sha256=artifact,
                provider=provider,
                budget=budget,
                per_call=per_call,
                check_use=guard,
            )
            student, lm = program(dspy, dispatch)
            predict = student.predict
            student.predict.signature = student.predict.signature.with_instructions(
                text
            )
            signature = student.predict.signature.with_instructions(text)
            score = 0
            try:
                with dspy.context(
                    lm=lm,
                    adapter=dspy.ChatAdapter(use_native_function_calling=False),
                    disable_history=True,
                    callbacks=[],
                ):
                    for example in heldout:
                        dispatch.guard()
                        prediction = student(case=case(example))
                        answer = prediction.answer.encode()
                        last = dispatch.attempts[-1]
                        if (
                            last.request.output_id,
                            last.request.output_revision,
                            last.content_sha256,
                        ) != (
                            example.output.output_id,
                            example.output.revision,
                            digest(answer),
                        ):
                            ledger.stop("prediction_receipt_mismatch")
                            raise ev.Halt("prediction_receipt_mismatch")
                        predictions.append(
                            (
                                stage,
                                example.output.output_id,
                                example.output.revision,
                                digest(answer),
                            )
                        )
                        score += int(answer == example.gold)
            finally:
                attempts.extend(dispatch.attempts)
            dispatch.guard()
            scores[stage] = score
        dispatch.guard()
        ledger.checked_snapshot()
    except Exception:
        ledger.stop("heldout_evaluation_failed")
        return {
            "status": "blocked",
            "scores": scores,
            "attempts": tuple(attempts),
            "predictions": tuple(predictions),
            "candidate_sha256": compiled.candidate_sha256,
            "usage": budget.snapshot(),
        }
    return {
        "status": "evaluated",
        "scores": scores,
        "attempts": tuple(attempts),
        "predictions": tuple(predictions),
        "candidate_sha256": compiled.candidate_sha256,
        "usage": budget.snapshot(),
        "quality": "synthetic_exact_match_only",
        "promotion": "not_authorized",
    }
