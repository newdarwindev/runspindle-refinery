"""Genuine BootstrapFewShot integration with a strictly offline transport.

The framework lane is separately qualified. Dependency-free transport tests are
not evidence that DSPy ran. No provider, reflection model or promotion authority
is accepted here; durable run ownership/recovery remains a separate obligation.
"""

from __future__ import annotations

import importlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from . import evaluation as ev
from .lineage import MAX_BYTES, Dataset, Example, LockedSplit, _token, digest
from .optimization import validate_dataset

VERSION = "refinery.dspy.bootstrap.v1"
DSPY_VERSION = "3.1.0"
SIGNATURE = "case -> answer"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def case(example: Example) -> str:
    return canonical(
        {
            "output_id": example.output.output_id,
            "revision": example.output.revision,
            "source_sha256": example.source.sha256,
            "source": example.source.content.decode("utf-8"),
        }
    ).decode()


@dataclass(frozen=True)
class Settings:
    max_bootstrapped_demos: int = 4
    max_rounds: int = 1

    def __post_init__(self) -> None:
        for value, maximum in ((self.max_bootstrapped_demos, 16), (self.max_rounds, 3)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid optimizer bound")


@dataclass(frozen=True)
class Compiled:
    status: str
    reason: str | None
    configuration_sha256: str
    candidate: bytes | None
    candidate_sha256: str | None
    attempts: tuple[ev.Attempt, ...]
    usage: tuple[tuple[str, Any], ...]
    evidence_class: str = "offline_dspy_bootstrap_component"


class Dispatch:
    """One admission/settlement path for every framework LM call, including copies.

    It owns no provider fallback. Copies of the DSPy LM close over this same object.
    Independent simultaneous programs need separate Dispatch objects; shared
    SQLiteBudget concurrency retains its existing cross-process contract.
    """

    def __init__(
        self,
        examples: tuple[Example, ...],
        split: LockedSplit,
        *,
        stage: str,
        artifact_sha256: str,
        provider: ev.ScriptedProvider,
        budget: ev.Budget,
        per_call: ev.Reservation,
        check_use: Callable[[], None],
    ) -> None:
        if type(provider) is not ev.ScriptedProvider or not isinstance(
            budget, ev.Budget
        ):
            raise ValueError("explicit offline adapters required")
        if (
            type(per_call) is not ev.Reservation
            or budget.clock_ms is not provider.clock
        ):
            raise ValueError("reservation or clock binding changed")
        if stage not in {"optimization", "baseline", "candidate"}:
            raise ValueError("invalid stage")
        partition = "train" if stage == "optimization" else "heldout"
        if not examples or any(
            split.partition(e.source.family_id) != partition for e in examples
        ):
            raise ValueError("forbidden partition admission")
        self.allowed = {case(e): e for e in examples}
        self.split, self.stage, self.artifact_sha256 = split, stage, artifact_sha256
        self.provider: Any = provider
        self.script_sha256 = provider.sha256
        self.expected_calls = tuple(provider.calls)
        self.clock = provider.clock
        self.limits_sha256 = digest(canonical(vars(budget.limits)))
        self.reservation_sha256 = digest(canonical(vars(per_call)))
        self.budget: Any = budget
        self.per_call, self.check_use = per_call, check_use
        self.in_forward: ContextVar[bool] = ContextVar(
            "dspy_forward_active", default=False
        )
        self.active: ContextVar[str | None] = ContextVar(
            "dspy_active_case", default=None
        )
        self.attempts: list[ev.Attempt] = []
        self.messages: list[str] = []
        self.counts: dict[str, int] = {}

    def check_binding(self) -> None:
        if (
            self.provider.sha256 != self.script_sha256
            or type(self.provider.calls) is not list
            or tuple(self.provider.calls) != self.expected_calls
            or self.provider.clock is not self.clock
            or self.budget.clock_ms is not self.clock
            or digest(canonical(vars(self.budget.limits))) != self.limits_sha256
            or digest(canonical(vars(self.per_call))) != self.reservation_sha256
        ):
            self.budget.stop("dispatch_binding_changed")
            # Preserve the first terminal cause without invoking a possibly
            # replaced clock or adopting a failed provider's changed cursor.
            reason = self.budget.snapshot().get("reason")
            if type(reason) is not str:
                reason = "dispatch_binding_changed"
            try:
                _token(reason)
            except ValueError:
                reason = "dispatch_binding_changed"
            raise ev.Halt(reason)

    def guard(self) -> None:
        self.check_binding()
        ev.guard_use(self.budget, self.check_use)
        self.check_binding()

    def invoke(self, predict: Callable[..., Any], value: str) -> Any:
        if self.active.get() is not None:
            self.budget.stop("reentrant_dispatch")
            raise ev.Halt("reentrant_dispatch")
        if value not in self.allowed:
            self.budget.stop("forbidden_partition")
            raise ev.Halt("forbidden_partition")
        token = self.active.set(value)
        try:
            return predict(case=value)
        finally:
            self.active.reset(token)

    def forward(self, messages: Any) -> Any:
        if self.in_forward.get():
            self.budget.stop("reentrant_dispatch")
            raise ev.Halt("reentrant_dispatch")
        token = self.in_forward.set(True)
        try:
            return self._forward(messages)
        finally:
            self.in_forward.reset(token)

    def _forward(self, messages: Any) -> Any:
        value = self.active.get()
        if value not in self.allowed:
            self.budget.stop("unattributed_dispatch")
            raise ev.Halt("unattributed_dispatch")
        example = self.allowed[value]
        self.counts[value] = self.counts.get(value, 0) + 1
        request = ev.Request(
            self.stage,
            example.output.output_id,
            example.output.revision,
            example.source.sha256,
            self.artifact_sha256,
            self.split.sha256,
            self.counts[value],
        )
        reply = self._complete(request, messages)
        return framed(reply, "answer")

    def reserve(self, request: ev.Request) -> Any:
        return self.budget.reserve(self.per_call, request=request)

    def _complete(self, request: ev.Request, messages: Any) -> ev.Completion:
        """Shared settlement only; framing and request admission belong to callers."""
        self.guard()
        ticket = self.reserve(request)
        message_sha256 = digest(canonical(messages))
        self.guard()
        self.messages.append(message_sha256)
        try:
            reply = self.provider.complete(request)
            self.expected_calls += (request,)
            self.check_binding()
        except Exception:
            self.budget.stop("provider_unknown")
            self.attempts.append(
                ev.Attempt(request, "unknown", None, None, None, None, None)
            )
            raise ev.Halt("provider_unknown") from None
        if (
            type(reply) is not ev.Completion
            or reply.outcome not in {"completed", "timeout"}
            or type(reply.latency_ms) is not int
            or not 0 <= reply.latency_ms <= 2**64 - 1
            or (type(reply.safety) is not bool and reply.safety is not None)
            or (
                reply.outcome == "completed"
                and (
                    type(reply.content) is not bytes
                    or not 0 < len(reply.content) <= MAX_BYTES
                )
            )
            or (reply.outcome == "timeout" and reply.content is not None)
        ):
            self.budget.stop("malformed_reply")
            self.attempts.append(
                ev.Attempt(request, "malformed", None, None, None, None, None)
            )
            raise ev.Halt("malformed_reply")
        attempt = ev.Attempt(
            request,
            reply.outcome,
            digest(reply.content) if reply.content is not None else None,
            reply.tokens,
            reply.cost_microunits,
            reply.safety,
            reply.latency_ms,
        )
        self.attempts.append(attempt)
        self.budget.settle(
            ticket,
            tokens=reply.tokens,
            cost_microunits=reply.cost_microunits,
            receipt=attempt,
        )
        if reply.safety is not True or reply.outcome != "completed":
            reason = (
                "timeout"
                if reply.outcome == "timeout"
                else ("unknown_safety" if reply.safety is None else "unsafe")
            )
            self.budget.stop(reason)
            raise ev.Halt(reason)
        self.guard()
        if reply.content is None:
            raise ev.Halt("malformed_reply")
        return reply


def framed(reply: ev.Completion, output: str) -> Any:
    """Format a previously settled response for a sealed output field."""
    if output not in {"answer", "instruction"} or reply.content is None:
        raise ValueError("invalid settled output")
    text = (
        "[[ ## "
        + output
        + " ## ]]\n"
        + reply.content.decode("utf-8")
        + "\n[[ ## completed ## ]]"
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage={"prompt_tokens": 0, "completion_tokens": reply.tokens},
        model="synthetic/local",
    )


def framework() -> Any:
    """Import only the existing pinned framework, never install or choose a fallback."""
    dspy = importlib.import_module("dspy")
    if dspy.__version__ != DSPY_VERSION:
        raise ValueError("unqualified DSPy version")
    return dspy


def program(dspy: Any, dispatch: Dispatch) -> tuple[Any, Any]:
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
            # Bootstrap retries must not deepcopy/refund a ledger or reset a cursor.
            return LocalLM(**kwargs)

    class Program(dspy.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.predict = dspy.Predict(SIGNATURE)

        def forward(self, case: str) -> Any:
            return dispatch.invoke(self.predict, case)

    return Program(), LocalLM()


def binding(
    dataset: Dataset,
    split: LockedSplit,
    settings: Settings,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
) -> tuple[tuple[Example, ...], str]:
    if type(provider) is not ev.ScriptedProvider or not isinstance(budget, ev.Budget):
        raise ValueError("explicit offline adapters required")
    if type(per_call) is not ev.Reservation or budget.clock_ms is not provider.clock:
        raise ValueError("reservation or clock binding changed")
    if type(settings) is not Settings:
        raise ValueError("invalid optimizer settings")
    settings.__post_init__()
    train, _ = validate_dataset(dataset, split)
    if not train or len(train) * settings.max_rounds > 4096:
        raise ValueError("invalid training size")
    # Decode all admitted training bytes before any call or claim.
    for example in train:
        case(example)
        example.gold.decode("utf-8")
    manifest = Path(__file__).resolve().parents[1] / "offline/dependencies.json"
    dependencies = json.loads(manifest.read_bytes())
    if dependencies["dspy"] != DSPY_VERSION:
        raise ValueError("dependency pin changed")
    config = digest(
        canonical(
            {
                "version": VERSION,
                "dspy": DSPY_VERSION,
                "dependencies_sha256": digest(manifest.read_bytes()),
                "settings": vars(settings),
                "dataset": split.dataset_sha256,
                "split": split.sha256,
                "script": provider.sha256,
                "limits": vars(budget.limits),
                "per_call": vars(per_call),
                "metric": "synthetic_exact_match.v1",
                "signature": SIGNATURE,
                "max_labeled_demos": 0,
                "max_errors": 1,
            }
        )
    )
    return train, config


def encode_candidate(
    demos: tuple[tuple[str, str], ...],
    train: tuple[Example, ...],
    split: LockedSplit,
    configuration_sha256: str,
) -> bytes:
    expected = {case(e): e.gold.decode("utf-8") for e in train}
    if (
        not demos
        or len(demos) > 16
        or len({v[0] for v in demos}) != len(demos)
        or any(expected.get(key) != answer for key, answer in demos)
    ):
        raise ValueError("unattributable compiled demonstrations")
    candidate = canonical(
        {
            "format": VERSION,
            "dspy": DSPY_VERSION,
            "signature": SIGNATURE,
            "dataset_sha256": split.dataset_sha256,
            "split_sha256": split.sha256,
            "configuration_sha256": configuration_sha256,
            "demonstrations": [
                {"case": key, "answer": answer} for key, answer in demos
            ],
        }
    )
    if len(candidate) > MAX_BYTES:
        raise ValueError("candidate size limit")
    return candidate


def decode_candidate(
    candidate: bytes,
    expected_sha256: str,
    dataset: Dataset,
    split: LockedSplit,
    configuration_sha256: str,
) -> tuple[tuple[str, str], ...]:
    if (
        type(candidate) is not bytes
        or len(candidate) > MAX_BYTES
        or digest(candidate) != expected_sha256
    ):
        raise ValueError("locked candidate digest changed")
    train, _ = validate_dataset(dataset, split)
    try:
        decoded = json.loads(candidate)
        demos = tuple((row["case"], row["answer"]) for row in decoded["demonstrations"])
        if encode_candidate(demos, train, split, configuration_sha256) != candidate:
            raise ValueError("candidate association changed")
    except (KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid candidate") from exc
    return demos


def optimize(
    dataset: Dataset,
    split: LockedSplit,
    *,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
    settings: Settings = Settings(),
    check_use: Callable[[], None],
) -> Compiled:
    ledger: Any = budget
    if type(settings) is not Settings:
        raise ValueError("invalid optimizer settings")
    settings = Settings(settings.max_bootstrapped_demos, settings.max_rounds)
    train, config = binding(dataset, split, settings, provider, budget, per_call)
    dispatch = Dispatch(
        train,
        split,
        stage="optimization",
        artifact_sha256=config,
        provider=provider,
        budget=budget,
        per_call=per_call,
        check_use=check_use,
    )
    if provider.calls:
        raise ValueError("optimizer requires pristine provider cursor")
    # Missing/wrong dependencies fail before any dispatch; never use a framework double.
    dispatch.guard()
    dspy = framework()
    student, lm = program(dspy, dispatch)
    examples = [
        dspy.Example(case=case(e), answer=e.gold.decode()).with_inputs("case")
        for e in train
    ]
    candidate, reason = None, None
    try:
        with dspy.context(
            lm=lm,
            adapter=dspy.ChatAdapter(use_native_function_calling=False),
            disable_history=True,
            callbacks=[],
        ):
            optimizer = dspy.BootstrapFewShot(
                metric=lambda gold, prediction, trace=None: gold.answer
                == prediction.answer,
                max_bootstrapped_demos=settings.max_bootstrapped_demos,
                max_labeled_demos=0,
                max_rounds=settings.max_rounds,
                max_errors=1,
            )
            compiled = optimizer.compile(student, trainset=examples)
        dispatch.guard()
        if len(compiled.predictors()) != 1 or not compiled.predict.signature.equals(
            student.predict.signature
        ):
            raise ValueError("optimizer changed fixed program")
        candidate = encode_candidate(
            tuple((demo.case, demo.answer) for demo in compiled.predict.demos),
            train,
            split,
            config,
        )
        if len(compiled.predict.demos) > settings.max_bootstrapped_demos:
            raise ValueError("optimizer exceeded demonstration bound")
        dispatch.guard()
        ledger.checked_snapshot()
    except ev.Halt as exc:
        reason, candidate = str(exc), None
    except Exception:
        ledger.stop("optimizer_failure")
        reason, candidate = "optimizer_failure", None
    return Compiled(
        "compiled" if candidate is not None else "blocked",
        reason,
        config,
        candidate,
        digest(candidate) if candidate is not None else None,
        tuple(dispatch.attempts),
        tuple(sorted(budget.snapshot().items())),
    )


def evaluate_locked(
    candidate: bytes,
    *,
    expected_sha256: str,
    configuration_sha256: str,
    dataset: Dataset,
    split: LockedSplit,
    provider: ev.ScriptedProvider,
    budget: ev.Budget,
    per_call: ev.Reservation,
    check_use: Callable[[], None],
    settings: Settings = Settings(),
) -> dict[str, Any]:
    """Evaluate a sealed candidate and fixed empty-demo baseline on held-out only.

    Caller must pass the same cumulative ledger as optimization. This function
    cannot create/reset budgets, recompile a candidate, promote it, or claim gains.
    It is process-local and does not enforce a global once-only evaluation policy.
    """
    ledger: Any = budget
    demos = decode_candidate(
        candidate, expected_sha256, dataset, split, configuration_sha256
    )
    train, expected_config = binding(
        dataset, split, settings, provider, budget, per_call
    )
    if expected_config != configuration_sha256:
        raise ValueError("evaluation configuration changed")
    allowed = {(e.output.output_id, e.output.revision, e.source.sha256) for e in train}
    if not provider.calls or any(
        r.stage != "optimization"
        or r.artifact_sha256 != configuration_sha256
        or r.split_sha256 != split.sha256
        or (r.output_id, r.output_revision, r.source_sha256) not in allowed
        for r in provider.calls
    ):
        raise ValueError("evaluation requires matching optimization cursor")
    if ledger.snapshot()["calls"] < len(provider.calls):
        raise ValueError("evaluation ledger lost optimization reservations")
    _, heldout = validate_dataset(dataset, split)
    if not heldout:
        raise ValueError("empty heldout")
    dspy = framework()
    scores: dict[str, int] = {}
    attempts: list[ev.Attempt] = []
    predictions: list[tuple[str, str, str, str]] = []
    try:
        for stage in ("baseline", "candidate"):
            artifact = (
                digest(canonical({"signature": SIGNATURE, "demonstrations": []}))
                if stage == "baseline"
                else expected_sha256
            )
            dispatch = Dispatch(
                heldout,
                split,
                stage=stage,
                artifact_sha256=artifact,
                provider=provider,
                budget=budget,
                per_call=per_call,
                check_use=check_use,
            )
            student, lm = program(dspy, dispatch)
            if stage == "candidate":
                student.predict.demos = [
                    dspy.Example(case=key, answer=answer) for key, answer in demos
                ]
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
            dispatch.guard()  # seal phase exit before accepting a new controller
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
            "candidate_sha256": expected_sha256,
            "usage": budget.snapshot(),
        }
    return {
        "status": "evaluated",
        "scores": scores,
        "attempts": tuple(attempts),
        "predictions": tuple(predictions),
        "candidate_sha256": expected_sha256,
        "usage": budget.snapshot(),
        "quality": "synthetic_exact_match_only",
        "promotion": "not_authorized",
    }
