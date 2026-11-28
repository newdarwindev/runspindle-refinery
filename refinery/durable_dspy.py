"""Durable ownership around the real, separately qualified DSPy adapter.

State/receipt tests need no DSPy and do not qualify framework execution. All rows
use the existing Store, codec, immutable objects and cumulative SQLiteBudget.
"""

from __future__ import annotations

from typing import Any, Callable, cast
from weakref import ReferenceType, WeakKeyDictionary, ref

from . import dspy_optimizer as opt
from . import evaluation as ev
from . import gepa_codec, gepa_measurements
from . import gepa_evidence as evidence
from . import gepa_optimizer as gepa
from . import heldout_policy as policy
from .durable_evaluation import SQLiteBudget, _restore
from .feedback_use import RetainedFeedbackUse, use_guard
from .lineage import Example, _token, digest
from .materialized_use import AuthorizedBatch, BoundBatch
from .optimization import validate_dataset
from .storage import Store, pack, unpack, version

LIFECYCLE = "refinery.dspy.lifecycle.v1"
MaterializedCases = tuple[str, tuple[tuple[str, str], ...]]

# Component-owned routing, not a resettable Session attribute. Weak values avoid
# retaining a Session through a test/authority callback that closes over it.
_GEPA_AUTHORITIES: WeakKeyDictionary[
    Any, tuple[tuple[str, ReferenceType[Any]], ...]
] = WeakKeyDictionary()


_MATERIALIZED: WeakKeyDictionary[Any, tuple[Any, ...]] = WeakKeyDictionary()


class Session:
    def __init__(
        self,
        store: Store,
        api_version: str,
        identity: str,
        snapshot_sha256: str,
        *,
        provider: ev.ScriptedProvider,
        limits: ev.Limits,
        per_call: ev.Reservation,
        budget_id: str,
        settings: opt.Settings = opt.Settings(),
        strategy: str = "bootstrap",
        training_output_id: str | None = None,
        retained_use: RetainedFeedbackUse | None = None,
        synthetic_fixture: bool = False,
        checkpoint: Callable[[str], None] = lambda _: None,
        materialized_batches: tuple[AuthorizedBatch, ...] = (),
    ) -> None:
        check_version: Any = version
        check_version(api_version)
        _token(identity)
        _token(budget_id)
        if (
            type(provider) is not ev.ScriptedProvider
            or type(provider.clock) is not ev.VirtualClock
        ):
            raise ValueError("explicit offline adapter required")
        if type(limits) is not ev.Limits or type(per_call) is not ev.Reservation:
            raise ValueError("invalid budget configuration")
        if type(settings) is not opt.Settings:
            raise ValueError("invalid optimizer settings")
        if type(strategy) is not str or strategy not in {"bootstrap", "gepa"}:
            raise ValueError("unknown sealed optimizer strategy")
        if strategy == "bootstrap" and training_output_id is not None:
            raise ValueError("Bootstrap does not accept GEPA selection")
        if strategy == "gepa" and (
            type(training_output_id) is not str or settings != opt.Settings()
        ):
            raise ValueError("explicit GEPA training selection required")
        self.strategy, self.training_output_id = strategy, training_output_id
        self.lifecycle = LIFECYCLE if strategy == "bootstrap" else evidence.LIFECYCLE
        self.compiled_kind = (
            "dspy_compiled" if strategy == "bootstrap" else "gepa_compiled"
        )
        self.store: Any = store
        self.provider: Any = provider
        self.clock = provider.clock
        self.limits, self.per_call, self.budget_id = limits, per_call, budget_id
        self.settings = opt.Settings(
            settings.max_bootstrapped_demos, settings.max_rounds
        )
        self.snapshot_sha256, self.checkpoint = snapshot_sha256, checkpoint
        db: Any = store
        self.dataset, self.split = db.lineage(snapshot_sha256)
        probe = ev.Budget(limits, clock_ms=provider.clock)
        if strategy == "bootstrap":
            self.train, self.config = opt.binding(
                self.dataset, self.split, self.settings, provider, probe, per_call
            )
        else:
            example, self.config = gepa.binding(
                self.dataset,
                self.split,
                training_output_id=cast(str, training_output_id),
                provider=provider,
                budget=probe,
                per_call=per_call,
            )
            self.train = (example,)
        self.key = ("dspy-" if strategy == "bootstrap" else "gepa-") + digest(
            identity.encode()
        )
        encode: Any = pack
        if type(materialized_batches) is not tuple or len(materialized_batches) > 2:
            raise ValueError("bounded materialized phase batches required")
        if materialized_batches and (
            synthetic_fixture or type(retained_use) is not RetainedFeedbackUse
        ):
            raise ValueError("materialized runs require retained authority")
        self._materialized_batches = tuple(
            BoundBatch(
                b, store, snapshot_sha256, cast(RetainedFeedbackUse, retained_use)
            )
            for b in materialized_batches
        )
        if len({b.purpose for b in self._materialized_batches}) != len(
            self._materialized_batches
        ):
            raise ValueError("duplicate materialized phase")
        if self._materialized_batches and "optimization" not in {
            b.purpose for b in self._materialized_batches
        }:
            raise ValueError("materialized training batch required")
        _MATERIALIZED[self] = tuple(
            (ref(b), b.seal()) for b in self._materialized_batches
        )
        self.binding: bytes = self.ownership_binding()

        self.binding_sha256 = digest(self.binding)
        self._strategy_seal = self.strategy_binding()
        self.guards = {
            purpose: self.make_use_guard(
                store,
                snapshot_sha256,
                purpose=purpose,
                configuration_sha256=self.config,
                run_sha256=digest(encode((self.key, self.binding))),
                retained_use=retained_use,
                synthetic_fixture=synthetic_fixture,
                policy_owner=self.key,
            )
            for purpose in (
                ("optimization", "evaluation")
                if strategy == "bootstrap"
                else ("optimization", "reflection", "evaluation")
            )
        }
        if strategy == "gepa":
            _GEPA_AUTHORITIES[self] = tuple(
                (p, ref(callback)) for p, callback in self.guards.items()
            )

    def batches(self) -> tuple[BoundBatch, ...]:
        registered = _MATERIALIZED.get(self)
        batches = self._materialized_batches
        if (
            registered is None
            or type(batches) is not tuple
            or len(registered) != len(batches)
            or any(
                reference() is not batch or batch.seal() != seal
                for batch, (reference, seal) in zip(batches, registered)
            )
        ):
            raise ev.Halt("materialized_authority_changed")
        return batches

    def ownership_binding(self) -> bytes:
        base = (self.lifecycle, self.snapshot_sha256, self.config, self.budget_id)
        batches = self.batches()
        encode: Any = pack
        return cast(
            bytes,
            encode(
                base
                if not batches
                else base
                + (
                    (
                        "materialized-phase-batches.v1",
                        tuple(sorted((b.purpose, b.identity) for b in batches)),
                    ),
                )
            ),
        )

    def make_use_guard(
        self, store: Store, snapshot: str, **kwargs: Any
    ) -> Callable[[], MaterializedCases | None]:
        if not self.batches():
            return use_guard(store, snapshot, **kwargs)
        purpose = kwargs["purpose"]
        phase = "optimization" if purpose == "reflection" else purpose

        def check() -> MaterializedCases | None:
            matches = [b for b in self.batches() if b.purpose == phase]
            if len(matches) != 1:
                raise ev.Halt("materialized_phase_batch_required")
            batch = matches[0]
            expected = (
                self.train
                if phase == "optimization"
                else validate_dataset(self.dataset, self.split)[1]
            )
            with batch.scope() as (registry, retained_use, selections):
                matched = batch.check(
                    registry,
                    selections,
                    purpose=purpose,
                    expected_examples=tuple(expected),
                )
                use_guard(store, snapshot, **dict(kwargs, retained_use=retained_use))()
                batch.fence(registry, selections)
                self.batches()
            if phase != "evaluation":
                return None
            cases = tuple(
                sorted(
                    (
                        gepa_measurements.case_digest(
                            example.output.output_id, example.output.revision
                        ),
                        item,
                    )
                    for item, example in matched
                )
            )
            return digest(batch.manifest), cases

        return check

    def verify_authority_routes(self) -> tuple[tuple[str, ReferenceType[Any]], ...]:
        if self.strategy != "gepa":
            return ()
        routes = _GEPA_AUTHORITIES.get(self)
        if (
            routes is None
            or type(self.guards) is not dict
            or set(self.guards) != {"optimization", "reflection", "evaluation"}
            or any(
                callback() is None or self.guards[p] is not callback()
                for p, callback in routes
            )
        ):
            raise ev.Halt("lifecycle_authority_changed")
        return routes

    def strategy_binding(self) -> bytes:
        """Closed selector admission, independent of callback-mutated branch labels."""
        if type(self.strategy) is not str or self.strategy not in {"bootstrap", "gepa"}:
            raise ev.Halt("lifecycle_strategy_changed")
        bootstrap = self.strategy == "bootstrap"
        lifecycle = LIFECYCLE if bootstrap else evidence.LIFECYCLE
        kind = "dspy_compiled" if bootstrap else "gepa_compiled"
        if (
            self.lifecycle != lifecycle
            or self.compiled_kind != kind
            or type(self.key) is not str
            or not self.key.startswith("dspy-" if bootstrap else "gepa-")
            or (bootstrap and self.training_output_id is not None)
            or (not bootstrap and type(self.training_output_id) is not str)
        ):
            raise ev.Halt("lifecycle_strategy_changed")
        encode: Any = pack
        if self.binding != self.ownership_binding() or self.binding_sha256 != digest(
            self.binding
        ):
            raise ev.Halt("lifecycle_strategy_changed")
        return cast(
            bytes,
            encode(
                (
                    self.strategy,
                    self.training_output_id,
                    self.lifecycle,
                    self.compiled_kind,
                    self.key,
                    self.binding,
                )
            ),
        )

    def cursor(self) -> tuple[ev.Request, ...]:
        if type(self.provider) is not ev.ScriptedProvider:
            raise ev.Halt("lifecycle_provider_changed")
        if (
            type(self.provider.calls) is not list
            or len(self.provider.calls) > 4096
            or any(type(r) is not ev.Request for r in self.provider.calls)
            or type(self.provider.script) is not tuple
            or len(self.provider.script) > 4096
            or any(type(r) is not ev.Completion for r in self.provider.script)
            or type(self.provider.clock) is not ev.VirtualClock
            or self.provider.clock is not self.clock
        ):
            raise ev.Halt("lifecycle_provider_changed")
        return tuple(self.provider.calls)

    def guard(self, purpose: str) -> None:
        self._guard(purpose)

    def _guard(self, purpose: str) -> MaterializedCases | None:
        seal = Session.strategy_binding(self)
        if seal != self._strategy_seal:
            raise ev.Halt("lifecycle_strategy_changed")
        authority_routes = Session.verify_authority_routes(self)
        strategy = self.strategy
        cursor = self.cursor()
        claim = self.row(self.store.db)
        if purpose not in {"optimization", "evaluation"}:
            raise ValueError("unknown durable purpose")
        purposes = (
            ("optimization", "reflection")
            if strategy == "gepa" and purpose == "optimization"
            else (purpose,)
        )
        guards = self.guards
        callbacks = tuple(guards[p] for p in purposes)
        materialized = None
        for callback in callbacks:
            Session.verify_authority_routes(self)
            materialized = callback()
            if Session.strategy_binding(self) != seal or self._strategy_seal != seal:
                raise ev.Halt("lifecycle_strategy_changed")
            if Session.verify_authority_routes(self) is not authority_routes:
                raise ev.Halt("lifecycle_authority_changed")
            if (
                Session.strategy_binding(self) != seal
                or self._strategy_seal != seal
                or self.guards is not guards
                or any(self.guards[p] is not c for p, c in zip(purposes, callbacks))
            ):
                raise ev.Halt("lifecycle_strategy_changed")
            if self.row(self.store.db) != claim:
                raise ev.Halt("lifecycle_claim_changed")
            if self.cursor() != cursor:
                raise ev.Halt("lifecycle_provider_changed")
        if self.row(self.store.db) != claim:
            raise ev.Halt("lifecycle_claim_changed")
        if self.cursor() != cursor:
            raise ev.Halt("lifecycle_provider_changed")
        probe = ev.Budget(self.limits, clock_ms=self.provider.clock)
        if self.strategy == "bootstrap":
            _, config = opt.binding(
                self.dataset,
                self.split,
                self.settings,
                self.provider,
                probe,
                self.per_call,
            )
        else:
            _, config = gepa.binding(
                self.dataset,
                self.split,
                training_output_id=cast(str, self.training_output_id),
                provider=self.provider,
                budget=probe,
                per_call=self.per_call,
            )
        if config != self.config:
            raise ev.Halt("lifecycle_configuration_changed")
        row = self.store.db.execute(
            "SELECT payload FROM budgets WHERE identity=?", (self.budget_id,)
        ).fetchone()
        decode: Any = unpack
        if row is not None and decode(row[0])["limits"] != self.limits:
            raise ev.Halt("lifecycle_configuration_changed")
        if (
            purpose == "evaluation"
            and claim is not None
            and claim[1] in {"evaluating", "complete"}
        ):
            compiled, _ = self.compiled()
            policy.verify_seal(
                self.store,
                self.dataset,
                self.split,
                owner=self.key,
                configuration_sha256=self.config,
                candidate_sha256=str(compiled.candidate_sha256),
            )
        return materialized

    def row(self, tx: Any) -> Any:
        row = tx.execute(
            "SELECT binding,state,report FROM experiments WHERE identity=?", (self.key,)
        ).fetchone()
        if row is not None and row[0] != self.binding:
            raise ValueError("immutable DSPy lifecycle conflict")
        return row

    def budget(self) -> Any:
        return SQLiteBudget(
            self.store,
            self.budget_id,
            self.limits,
            clock_ms=self.provider.clock,
            experiment=self.key,
            checkpoint=self.checkpoint,
        )

    def receipts(self, stages: set[str]) -> tuple[tuple[Any, Any, Any], ...]:
        decode: Any = unpack
        rows = tuple(
            (decode(request), outcome, decode(receipt) if receipt is not None else None)
            for request, outcome, receipt in self.store.db.execute(
                "SELECT request,outcome,receipt FROM attempts WHERE experiment=? ORDER BY ticket",
                (self.key,),
            )
        )
        if any(
            type(request) is not ev.Request
            or request.stage
            not in (
                {"optimization", "baseline", "candidate"}
                if self.strategy == "bootstrap"
                else {"optimization", "reflection", "baseline", "candidate"}
            )
            for request, _, _ in rows
        ):
            raise ValueError("invalid durable DSPy request")
        if self.strategy == "gepa":
            evaluation_seen = False
            candidate_seen = False
            compile_position = 0
            evaluation_counts: dict[tuple[str, str, str], int] = {}
            for request, _, _ in rows:
                if type(request.attempt) is not int:
                    raise ValueError("GEPA phase cursor changed")
                if request.stage in {"baseline", "candidate"}:
                    key = (request.stage, request.output_id, request.output_revision)
                    evaluation_counts[key] = evaluation_counts.get(key, 0) + 1
                    if request.attempt != evaluation_counts[key]:
                        raise ValueError("GEPA evaluation cursor changed")
                    evaluation_seen = True
                    if request.stage == "candidate":
                        candidate_seen = True
                    elif candidate_seen:
                        raise ValueError("GEPA baseline follows candidate")
                else:
                    if evaluation_seen:
                        raise ValueError("GEPA compilation follows heldout evaluation")
                    compile_position += 1
                    if request.attempt != compile_position:
                        raise ValueError("GEPA compile cursor changed")
        return tuple(row for row in rows if row[0].stage in stages)

    def verify_attempts(
        self, attempts: tuple[ev.Attempt, ...], stages: set[str], *, success: bool
    ) -> None:
        rows = self.receipts(stages)
        if type(attempts) is not tuple:
            raise ValueError("invalid DSPy attempts")
        examples = (
            self.train
            if stages <= {"optimization", "reflection"}
            else validate_dataset(self.dataset, self.split)[1]
        )
        allowed = {
            (e.output.output_id, e.output.revision, e.source.sha256) for e in examples
        }
        position = 0
        for request, outcome, receipt in rows:
            if (
                request.stage not in stages
                or request.split_sha256 != self.split.sha256
                or (request.output_id, request.output_revision, request.source_sha256)
                not in allowed
                or type(request.attempt) is not int
                or not 1 <= request.attempt <= 4096
            ):
                raise ValueError("DSPy request association changed")
            if (
                self.strategy == "bootstrap"
                and request.stage == "optimization"
                and request.artifact_sha256 != self.config
            ):
                raise ValueError("DSPy compile artifact changed")
            if position >= len(attempts) or attempts[position].request != request:
                if not success and outcome == "unknown" and receipt is None:
                    continue  # durable reservation may precede any observed dispatch
                raise ValueError("DSPy receipt count changed")
            attempt = attempts[position]
            position += 1
            if (
                type(attempt) is not ev.Attempt
                or (receipt is not None and receipt != attempt)
                or (
                    success
                    and (
                        outcome != "settled"
                        or receipt != attempt
                        or attempt.safety is not True
                        or attempt.outcome != "completed"
                    )
                )
            ):
                raise ValueError("DSPy receipt association changed")
        if position != len(attempts):
            raise ValueError("DSPy receipt count changed")

    def verify_script(self, attempts: tuple[ev.Attempt, ...]) -> None:
        if len(self.provider.script) < len(attempts):
            raise ValueError("resumed script exhausted")
        for reply, attempt in zip(self.provider.script, attempts):
            if (
                reply.outcome != "completed"
                or type(reply.content) is not bytes
                or digest(reply.content) != attempt.content_sha256
                or (reply.tokens, reply.cost_microunits, reply.safety, reply.latency_ms)
                != (
                    attempt.tokens,
                    attempt.cost_microunits,
                    attempt.safety,
                    attempt.latency_ms,
                )
            ):
                raise ValueError("immutable script receipt changed")

    def compiled(self) -> tuple[opt.Compiled | evidence.GEPACompiled, int]:
        value = self.store.get(self.compiled_kind, self.key)
        if type(value) is not dict or set(value) != {
            "version",
            "binding_sha256",
            "result",
            "clock_ms",
        }:
            raise ValueError("invalid compiled checkpoint")
        encode: Any = pack
        row = self.row(self.store.db)
        if row is None or (
            row[1] in {"compiled", "compile_blocked", "evaluating"}
            and row[2] != digest(encode(value))
        ):
            raise ValueError("compiled result association changed")
        result = value["result"]
        if (
            value["version"] != self.lifecycle
            or value["binding_sha256"] != self.binding_sha256
            or type(result)
            is not (
                opt.Compiled if self.strategy == "bootstrap" else evidence.GEPACompiled
            )
            or result.configuration_sha256 != self.config
            or result.evidence_class
            != (
                "offline_dspy_bootstrap_component"
                if self.strategy == "bootstrap"
                else "offline_gepa_compile_component"
            )
            or type(value["clock_ms"]) is not int
            or not 0 <= value["clock_ms"] <= 2**64 - 1
        ):
            raise ValueError("compiled checkpoint association changed")
        if result.status == "compiled":
            if (
                result.reason is not None
                or result.candidate is None
                or result.candidate_sha256 is None
            ):
                raise ValueError("invalid compiled candidate")
            if self.strategy == "gepa":
                gepa_codec.decode(
                    result.candidate,
                    result.candidate_sha256,
                    self.dataset,
                    self.split,
                    self.config,
                    result.attempts,
                )
            else:
                demos = opt.decode_candidate(
                    result.candidate,
                    result.candidate_sha256,
                    self.dataset,
                    self.split,
                    self.config,
                )
                if (
                    len(demos) > self.settings.max_bootstrapped_demos
                    or not result.attempts
                ):
                    raise ValueError("invalid compiled demonstration count")
                accepted = {
                    (opt.case(e), e.gold.decode())
                    for e in self.train
                    if any(
                        a.request.output_id == e.output.output_id
                        and a.request.output_revision == e.output.revision
                        and a.content_sha256 == digest(e.gold)
                        for a in result.attempts
                    )
                }
                if not set(demos) <= accepted:
                    raise ValueError("compiled demonstrations lack receipts")
            self.verify_script(result.attempts)
        elif (
            result.status != "blocked"
            or type(result.reason) is not str
            or not result.reason
            or result.candidate is not None
            or result.candidate_sha256 is not None
        ):
            raise ValueError("invalid compiled checkpoint status")
        self.verify_stored_usage(
            dict(result.usage), success=result.status == "compiled"
        )
        if tuple(sorted(dict(result.usage).items())) != result.usage:
            raise ValueError("invalid compiled usage")
        self.verify_attempts(
            result.attempts,
            (
                {"optimization"}
                if self.strategy == "bootstrap"
                else {"optimization", "reflection"}
            ),
            success=result.status == "compiled",
        )
        if self.strategy == "gepa":
            rows = self.receipts({"optimization", "reflection"})
            tickets = tuple(
                (ticket,)
                for ticket, request in self.store.db.execute(
                    "SELECT ticket,request FROM attempts WHERE experiment=? ORDER BY ticket",
                    (self.key,),
                )
                if unpack(request).stage in {"optimization", "reflection"}
            )
            prefix = self.key + ":"
            stored_ids = {
                r[0]
                for r in self.store.db.execute(
                    "SELECT identity FROM objects WHERE kind='gepa_request_witness' AND substr(identity,1,?)=?",
                    (len(prefix), prefix),
                )
            }
            if stored_ids != {prefix + str(t[0]) for t in tickets}:
                raise ValueError("GEPA witness reservation set changed")
            witnesses = tuple(
                self.store.get("gepa_request_witness", self.key + ":" + str(t[0]))
                for t in tickets
            )
            evidence.validate_history(
                rows,
                witnesses,
                self.train[0],
                self.split,
                self.config,
                self.provider.script,
            )
        self.verify_usage_totals(
            dict(result.usage), result.attempts, success=result.status == "compiled"
        )
        elapsed = dict(result.usage)["elapsed_ms"]
        if elapsed is not None and value["clock_ms"] < elapsed:
            raise ValueError("compiled clock association changed")
        return result, value["clock_ms"]

    @staticmethod
    def verify_stored_usage(usage: Any, *, success: bool) -> None:
        if type(usage) is not dict or set(usage) != {
            "calls",
            "tokens",
            "elapsed_ms",
            "cost_microunits",
            "unresolved_calls",
            "reason",
        }:
            raise ValueError("invalid DSPy usage")
        for key in (
            "calls",
            "tokens",
            "elapsed_ms",
            "cost_microunits",
            "unresolved_calls",
        ):
            value = usage[key]
            if (
                value is None
                and not success
                and key in {"tokens", "elapsed_ms", "cost_microunits"}
            ):
                continue
            if type(value) is not int or not 0 <= value <= 2**64 - 1:
                raise ValueError("invalid DSPy usage")
        if success and (usage["reason"] is not None or usage["unresolved_calls"] != 0):
            raise ValueError("unresolved DSPy usage")
        if not success and (type(usage["reason"]) is not str or not usage["reason"]):
            raise ValueError("missing blocked DSPy reason")

    def verify_usage_totals(
        self, usage: dict[str, Any], attempts: tuple[ev.Attempt, ...], *, success: bool
    ) -> None:
        if not len(attempts) <= usage["calls"] <= self.limits.calls:
            raise ValueError("DSPy usage call count changed")
        for name, limit, maximum in (
            ("tokens", self.limits.tokens, self.per_call.tokens),
            (
                "cost_microunits",
                self.limits.cost_microunits,
                self.per_call.cost_microunits,
            ),
        ):
            values = [getattr(a, name) for a in attempts]
            if success and any(
                type(v) is not int or not 0 <= v <= maximum for v in values
            ):
                raise ValueError("invalid successful DSPy receipt usage")
            known = sum(v for v in values if type(v) is int and v >= 0)
            if usage[name] is not None and not known <= usage[name] <= limit:
                raise ValueError("DSPy usage totals changed")
        if success and usage["elapsed_ms"] >= self.limits.time_ms:
            raise ValueError("DSPy elapsed budget exceeded")

    def verify_current_usage(self, usage: Any, *, success: bool) -> None:
        decode: Any = unpack
        restore: Any = _restore
        row = self.store.db.execute(
            "SELECT payload FROM budgets WHERE identity=?", (self.budget_id,)
        ).fetchone()
        if row is None:
            raise ValueError("missing shared DSPy ledger")
        state = decode(row[0])
        if state["limits"] != self.limits:
            raise ValueError("shared DSPy limits changed")
        ledger = restore(state, self.provider.clock)
        current = ledger.checked_snapshot() if success else ledger.snapshot()
        if type(usage) is not dict or current != usage:
            raise ValueError("DSPy usage association changed")

    def verify_cursor(self, attempts: tuple[ev.Attempt, ...]) -> None:
        if self.cursor() != tuple(a.request for a in attempts):
            raise ev.Halt("lifecycle_provider_changed")

    def recover_compiled(self) -> opt.Compiled | evidence.GEPACompiled | None:
        with self.store.transaction() as tx:
            row = self.row(tx)
            if row is None:
                return None
            if row[1] in {"compiling", "evaluating"}:
                raise ev.Halt("interrupted_or_owned_dspy_phase")
            if row[1] not in {"compiled", "compile_blocked", "complete"}:
                raise ValueError("invalid DSPy lifecycle state")
            self.guard("evaluation" if row[1] == "complete" else "optimization")
            result, _ = self.compiled()
            if self.row(tx) != row:
                raise ev.Halt("lifecycle_claim_changed")
            if (row[1] == "compile_blocked") != (result.status == "blocked"):
                raise ValueError("compiled state mismatch")
            return result

    def claim_compile(self) -> opt.Compiled | None:
        self.guard("optimization")
        with self.store.transaction() as tx:
            self.guard("optimization")
            row = self.row(tx)
            if row is not None:
                raise ev.Halt("interrupted_or_owned_dspy_phase")
            if self.provider.calls:
                raise ValueError("new DSPy claim requires pristine provider")
            policy.enroll(
                self.store,
                tx,
                self.dataset,
                self.split,
                owner=self.key,
                configuration_sha256=self.config,
            )
            # Shared ledger initialization and ownership commit atomically.
            self.budget()
            self.store.room(tx, "experiments")
            tx.execute(
                "INSERT INTO experiments VALUES(?,?,'compiling',NULL)",
                (self.key, self.binding),
            )
            self.guard("optimization")
            if self.row(tx) != (self.binding, "compiling", None):
                raise ev.Halt("lifecycle_claim_changed")
            if self.provider.calls:
                raise ev.Halt("lifecycle_provider_changed")
        self.checkpoint("after_dspy_compile_claim")
        self.guard("optimization")
        if (
            self.row(self.store.db) != (self.binding, "compiling", None)
            or self.provider.calls
        ):
            raise ev.Halt("lifecycle_claim_changed")
        return None

    def commit_compiled(self, result: opt.Compiled | evidence.GEPACompiled) -> None:
        encode: Any = pack
        result_bytes = encode(result)
        self.guard("optimization")
        with self.store.transaction() as tx:
            row = self.row(tx)
            if row is None or row[1] != "compiling":
                raise ev.Halt("DSPy compile ownership unavailable")
            record = {
                "version": self.lifecycle,
                "binding_sha256": self.binding_sha256,
                "result": result,
                "clock_ms": self.provider.clock(),
            }
            sha = self.store.put(tx, self.compiled_kind, self.key, record)
            self.verify_result_identity(
                self.compiled_kind, "result", result, result_bytes, sha
            )
            verified, _ = self.compiled()
            if encode(verified) != encode(result):
                raise ValueError("compiled result changed")
            self.verify_current_usage(
                dict(result.usage), success=result.status == "compiled"
            )
            self.verify_cursor(result.attempts)
            state = "compiled" if result.status == "compiled" else "compile_blocked"
            tx.execute(
                "UPDATE experiments SET state=?,report=? WHERE identity=?",
                (state, sha, self.key),
            )
            self.checkpoint("before_dspy_compile_commit")
            self.guard("optimization")
            if self.row(tx) != (self.binding, state, sha):
                raise ev.Halt("lifecycle_claim_changed")
            self.verify_current_usage(
                dict(result.usage), success=result.status == "compiled"
            )
            self.verify_cursor(result.attempts)
            self.verify_result_identity(
                self.compiled_kind, "result", result, result_bytes, sha
            )
            self.compiled()
        self.checkpoint("after_dspy_compile_commit")
        self.guard("optimization")
        self.verify_cursor(result.attempts)
        if self.row(self.store.db) != (self.binding, state, sha):
            raise ev.Halt("lifecycle_claim_changed")
        self.verify_current_usage(
            dict(result.usage), success=result.status == "compiled"
        )
        self.verify_result_identity(
            self.compiled_kind, "result", result, result_bytes, sha
        )
        self.compiled()

    def verify_result_identity(
        self,
        kind: str,
        field: str,
        result: Any,
        expected_bytes: bytes,
        record_sha256: str,
    ) -> None:
        """Callback-free equality of caller result and its exact durable record.

        Capture the caller's canonical bytes before any authority/checkpoint
        callback. A valid but different result is not a replacement approval.
        This check does not rewrite previously committed historical evidence.
        """
        encode: Any = pack
        record = self.store.get(kind, self.key)
        if (
            encode(result) != expected_bytes
            or digest(encode(record)) != record_sha256
            or encode(record[field]) != expected_bytes
        ):
            raise ValueError("DSPy result identity changed")

    def compile(self) -> opt.Compiled | evidence.GEPACompiled:
        cached = self.recover_compiled()
        if cached is not None:
            return cached
        result: opt.Compiled | evidence.GEPACompiled
        if self.strategy == "gepa":
            gepa.runtime_identity()
            opt.framework()
            self.claim_compile()
            raw = gepa.optimize(
                self.dataset,
                self.split,
                training_output_id=cast(str, self.training_output_id),
                provider=self.provider,
                budget=self.budget(),
                per_call=self.per_call,
                check_use=lambda: self.guard("optimization"),
            )
            result = evidence.GEPACompiled(
                raw.status,
                raw.reason,
                raw.configuration_sha256,
                raw.candidate,
                raw.candidate_sha256,
                raw.attempts,
                raw.usage,
            )
        else:
            # Missing framework cannot consume a claim or ledger. No fake fallback.
            opt.framework()
            self.claim_compile()
            result = opt.optimize(
                self.dataset,
                self.split,
                provider=self.provider,
                budget=self.budget(),
                per_call=self.per_call,
                settings=self.settings,
                check_use=lambda: self.guard("optimization"),
            )
        self.commit_compiled(result)
        return result

    def verify_evaluated(
        self, report: dict[str, Any], compiled: opt.Compiled | evidence.GEPACompiled
    ) -> None:
        if type(report) is not dict or report.get("status") not in {
            "evaluated",
            "blocked",
        }:
            raise ValueError("invalid heldout result")
        expected = {
            "status",
            "scores",
            "attempts",
            "candidate_sha256",
            "usage",
            "predictions",
        }
        if report["status"] == "evaluated":
            expected |= {"quality", "promotion"}
        if set(report) != expected:
            raise ValueError("invalid heldout result fields")
        self.verify_stored_usage(
            report.get("usage"), success=report["status"] == "evaluated"
        )
        if report.get("candidate_sha256") != compiled.candidate_sha256:
            raise ValueError("heldout candidate changed")
        attempts = report.get("attempts")
        if type(attempts) is not tuple:
            raise ValueError("invalid heldout attempts")
        self.verify_attempts(
            attempts, {"baseline", "candidate"}, success=report["status"] == "evaluated"
        )
        baseline_sha = (
            gepa_codec.baseline_artifact()
            if self.strategy == "gepa"
            else digest(
                opt.canonical({"signature": opt.SIGNATURE, "demonstrations": []})
            )
        )
        if any(
            a.request.artifact_sha256
            != (
                baseline_sha
                if a.request.stage == "baseline"
                else compiled.candidate_sha256
            )
            for a in attempts
        ):
            raise ValueError("heldout artifact association changed")
        self.verify_usage_totals(
            report["usage"],
            compiled.attempts + attempts,
            success=report["status"] == "evaluated",
        )
        heldout = validate_dataset(self.dataset, self.split)[1]
        predictions = report.get("predictions")
        if type(predictions) is not tuple:
            raise ValueError("invalid heldout prediction evidence")
        gold = {
            (e.output.output_id, e.output.revision): digest(e.gold) for e in heldout
        }
        observed: dict[tuple[str, str, str], str] = {}
        for prediction in predictions:
            if (
                type(prediction) is not tuple
                or len(prediction) != 4
                or any(type(v) is not str for v in prediction)
            ):
                raise ValueError("invalid heldout prediction evidence")
            stage, output_id, revision, answer = prediction
            key = (stage, output_id, revision)
            matching = [
                a
                for a in attempts
                if (a.request.stage, a.request.output_id, a.request.output_revision)
                == key
            ]
            if (
                stage not in {"baseline", "candidate"}
                or (output_id, revision) not in gold
                or key in observed
                or not matching
                or matching[-1].content_sha256 != answer
                or matching[-1].outcome != "completed"
                or matching[-1].safety is not True
            ):
                raise ValueError("prediction receipt association changed")
            observed[key] = answer
        scores = report.get("scores")
        if (
            type(scores) is not dict
            or not set(scores) <= {"baseline", "candidate"}
            or any(
                type(v) is not int or not 0 <= v <= len(heldout)
                for v in scores.values()
            )
        ):
            raise ValueError("invalid heldout scores")
        for stage, score in scores.items():
            measured = {
                (output_id, revision): answer
                for (kind, output_id, revision), answer in observed.items()
                if kind == stage
            }
            if set(measured) != set(gold) or score != sum(
                answer == gold[key] for key, answer in measured.items()
            ):
                raise ValueError("heldout scores lack prediction evidence")
        policy.verify_seal(
            self.store,
            self.dataset,
            self.split,
            owner=self.key,
            configuration_sha256=self.config,
            candidate_sha256=str(compiled.candidate_sha256),
        )
        if report["status"] == "evaluated":
            self.verify_script(compiled.attempts + attempts)
            identities = {(e.output.output_id, e.output.revision) for e in heldout}
            if (
                set(scores) != {"baseline", "candidate"}
                or report.get("quality") != "synthetic_exact_match_only"
                or report.get("promotion") != "not_authorized"
                or any(
                    {
                        (a.request.output_id, a.request.output_revision)
                        for a in attempts
                        if a.request.stage == stage
                    }
                    != identities
                    for stage in ("baseline", "candidate")
                )
            ):
                raise ValueError("incomplete heldout result")

    def _read_evaluated(
        self, tx: Any, *, include_materialized: bool = False
    ) -> tuple[dict[str, Any], str, MaterializedCases | None] | None:
        row = self.row(tx)
        if row is None or row[1] in {"compiling", "evaluating"}:
            raise ev.Halt("interrupted_or_owned_dspy_phase")
        if row[1] == "compile_blocked":
            raise ev.Halt("DSPy_compilation_blocked")
        if row[1] == "compiled":
            return None
        if row[1] != "complete":
            raise ValueError("invalid DSPy lifecycle state")
        materialized = None
        if include_materialized:
            materialized = self._guard("evaluation")
        else:
            self.guard("evaluation")
        compiled, _ = self.compiled()
        value = self.store.get(Session.evaluated_kind(self), self.key)
        encode: Any = pack
        if (
            type(value) is not dict
            or set(value) != {"version", "binding_sha256", "report"}
            or value["version"] != self.lifecycle
            or value["binding_sha256"] != self.binding_sha256
            or digest(encode(value)) != row[2]
        ):
            raise ValueError("heldout result association changed")
        self.verify_evaluated(value["report"], compiled)
        if self.row(tx) != row:
            raise ev.Halt("lifecycle_claim_changed")
        return value["report"], row[2], materialized

    def recover_evaluated(self) -> dict[str, Any] | None:
        with self.store.transaction() as tx:
            verified = self._read_evaluated(tx)
            return None if verified is None else verified[0]

    def measured_heldout(self, *, include_cohort: bool = False) -> dict[str, Any]:
        """Project exact retained GEPA attempt usage without starting evaluation."""
        return self._read_measurement(include_cohort=include_cohort)[0]

    def measured_materialized_heldout(
        self, *, include_cohort: bool = False
    ) -> dict[str, Any]:
        """Bind verified measured cases to their exact authorized materialized items."""
        measurement, heldout, _ = self._read_measurement(
            include_materialized=True, include_cohort=include_cohort
        )
        return self._materialized_report(measurement, heldout)

    def measured_materialized_cohorts(self) -> dict[str, Any]:
        """Include the retained optimization case without reauthorizing training."""
        measurement, heldout, training = self._read_measurement(
            include_materialized=True,
            include_cohort=True,
            include_training_mapping=True,
        )
        result = self._materialized_report(measurement, heldout)
        if training is None:
            raise ValueError("exact retained materialized training cohort required")
        manifest, cases = training
        return {
            **result,
            "version": "runspindle.gepa-materialized-cohort-measurement.v1",
            "training_manifest_sha256": manifest,
            "training_cases": [{"case": case, "item_id": item} for case, item in cases],
        }

    @staticmethod
    def _materialized_report(
        measurement: dict[str, Any], heldout: MaterializedCases | None
    ) -> dict[str, Any]:
        if heldout is None:
            raise ValueError("authorized materialized evaluation batch required")
        manifest, cases = heldout
        return {
            "version": "runspindle.gepa-materialized-measurement.v1",
            "measurement": measurement,
            "heldout_manifest_sha256": manifest,
            "cases": [{"case": case, "item_id": item} for case, item in cases],
        }

    def _retained_training_cases(self, train: tuple[Example, ...]) -> MaterializedCases:
        # The original optimization guard checked the single selected item
        # against self.train. The verified compile and ownership binding pin
        # that example and this immutable batch. Do not reopen its registry.
        batches = [batch for batch in self.batches() if batch.purpose == "optimization"]
        if (
            len(batches) != 1
            or len(batches[0].batch.selections) != 1
            or len(train) != 1
            or train != self.train
        ):
            raise ValueError("exact retained materialized training cohort required")
        batch = batches[0]
        case = gepa_measurements.case_digest(
            train[0].output.output_id, train[0].output.revision
        )
        return digest(batch.manifest), ((case, batch.batch.selections[0].item_id),)

    def _read_measurement(
        self,
        *,
        include_materialized: bool = False,
        include_cohort: bool = False,
        include_training_mapping: bool = False,
    ) -> tuple[dict[str, Any], MaterializedCases | None, MaterializedCases | None]:
        if Session.evaluated_kind(self) != "gepa_evaluated":
            raise ValueError("exact durable GEPA session required")
        with self.store.transaction() as tx:
            verified = self._read_evaluated(
                tx, include_materialized=include_materialized
            )
            if verified is None or verified[0]["status"] != "evaluated":
                raise ValueError("completed GEPA heldout measurement required")
            report, report_sha, materialized = verified
            train, heldout = validate_dataset(self.dataset, self.split)
            measurement = {
                "version": "runspindle.gepa-heldout-measurement.v1",
                "evidence_class": "offline_scripted_provider",
                "quality": report["quality"],
                "promotion": report["promotion"],
                "binding_sha256": self.binding_sha256,
                "configuration_sha256": self.config,
                "split_sha256": self.split.sha256,
                "report_sha256": report_sha,
                "baseline_sha256": gepa_codec.baseline_artifact(),
                "candidate_sha256": report["candidate_sha256"],
                "rows": gepa_measurements.paired_rows(
                    heldout, report["attempts"], report["predictions"]
                ),
            }
            if include_cohort:
                measurement["version"] = "runspindle.gepa-heldout-measurement.v2"
                measurement["cohort"] = {
                    "training_cases": gepa_measurements.case_roster(train),
                    "heldout_cases": gepa_measurements.case_roster(heldout),
                }
            training = (
                self._retained_training_cases(train)
                if include_training_mapping
                else None
            )
            return measurement, materialized, training

    def claim_evaluation(self) -> opt.Compiled | evidence.GEPACompiled:
        self.guard("evaluation")
        with self.store.transaction() as tx:
            row = self.row(tx)
            if row is None or row[1] != "compiled":
                raise ev.Halt("interrupted_or_owned_dspy_phase")
            compiled, clock_ms = self.compiled()
            if compiled.status != "compiled":
                raise ev.Halt("DSPy compilation blocked")
            if self.provider.clock() < clock_ms:
                raise ev.Halt("invalid_clock")
            prefix = tuple(a.request for a in compiled.attempts)
            if self.provider.calls and tuple(self.provider.calls) != prefix:
                raise ValueError("resumed provider cursor changed")
            # Verify the immutable script's exact prefix against settled receipts.
            self.verify_script(compiled.attempts)
            self.guard("evaluation")
            before_cursor = tuple(self.provider.calls)
            # Current shared ledger must be resolved and uncancelled before sealing.
            decode: Any = unpack
            restore: Any = _restore
            state = decode(
                tx.execute(
                    "SELECT payload FROM budgets WHERE identity=?", (self.budget_id,)
                ).fetchone()[0]
            )
            restore(state, self.provider.clock).checked_snapshot()
            policy.seal(
                self.store,
                tx,
                self.dataset,
                self.split,
                owner=self.key,
                configuration_sha256=self.config,
                candidate_sha256=str(compiled.candidate_sha256),
            )
            tx.execute(
                "UPDATE experiments SET state='evaluating' WHERE identity=?",
                (self.key,),
            )
            self.checkpoint("before_dspy_evaluation_claim_commit")
            self.guard("evaluation")
            if self.row(tx) != (self.binding, "evaluating", row[2]):
                raise ev.Halt("lifecycle_claim_changed")
            if tuple(self.provider.calls) != before_cursor:
                raise ev.Halt("lifecycle_provider_changed")
        # Restoring known synthetic history is not replaying its provider effects.
        self.provider.calls[:] = prefix
        self.checkpoint("after_dspy_evaluation_claim")
        self.guard("evaluation")
        self.verify_cursor(compiled.attempts)
        if self.row(self.store.db) != (self.binding, "evaluating", row[2]):
            raise ev.Halt("lifecycle_claim_changed")
        return compiled

    def commit_evaluated(self, report: dict[str, Any]) -> None:
        encode: Any = pack
        report_bytes = encode(report)
        self.guard("evaluation")
        with self.store.transaction() as tx:
            row = self.row(tx)
            if row is None or row[1] != "evaluating":
                raise ev.Halt("DSPy evaluation ownership unavailable")
            compiled, _ = self.compiled()
            self.verify_evaluated(report, compiled)
            self.verify_current_usage(
                report.get("usage"), success=report["status"] == "evaluated"
            )
            self.verify_cursor(compiled.attempts + report["attempts"])
            record = {
                "version": self.lifecycle,
                "binding_sha256": self.binding_sha256,
                "report": report,
            }
            sha = self.store.put(tx, Session.evaluated_kind(self), self.key, record)
            self.verify_result_identity(
                Session.evaluated_kind(self), "report", report, report_bytes, sha
            )
            tx.execute(
                "UPDATE experiments SET state='complete',report=? WHERE identity=?",
                (sha, self.key),
            )
            self.checkpoint("before_dspy_evaluation_commit")
            self.guard("evaluation")
            if self.row(tx) != (self.binding, "complete", sha):
                raise ev.Halt("lifecycle_claim_changed")
            self.verify_current_usage(
                report.get("usage"), success=report["status"] == "evaluated"
            )
            self.verify_cursor(compiled.attempts + report["attempts"])
            self.verify_result_identity(
                Session.evaluated_kind(self), "report", report, report_bytes, sha
            )
            self.verify_evaluated(report, compiled)
        self.checkpoint("after_dspy_evaluation_commit")
        self.guard("evaluation")
        self.verify_cursor(compiled.attempts + report["attempts"])
        if self.row(self.store.db) != (self.binding, "complete", sha):
            raise ev.Halt("lifecycle_claim_changed")
        self.verify_evaluated(report, compiled)
        self.verify_current_usage(
            report["usage"], success=report["status"] == "evaluated"
        )
        self.verify_result_identity(
            Session.evaluated_kind(self), "report", report, report_bytes, sha
        )

    def evaluated_kind(self) -> str:
        if Session.strategy_binding(self) != self._strategy_seal:
            raise ev.Halt("lifecycle_strategy_changed")
        return "gepa_evaluated" if self.strategy == "gepa" else "dspy_evaluated"

    def evaluate(self) -> dict[str, Any]:
        cached = self.recover_evaluated()
        if cached is not None:
            return cached
        if self.strategy == "gepa":
            gepa.runtime_identity()
        opt.framework()
        compiled = self.claim_evaluation()
        if compiled.candidate is None or compiled.candidate_sha256 is None:
            raise ValueError("missing compiled candidate")
        if self.strategy == "gepa":
            report = gepa.evaluate_locked(
                compiled,
                dataset=self.dataset,
                split=self.split,
                training_output_id=cast(str, self.training_output_id),
                provider=self.provider,
                budget=self.budget(),
                per_call=self.per_call,
                check_use=lambda: self.guard("evaluation"),
            )
        else:
            report = opt.evaluate_locked(
                compiled.candidate,
                expected_sha256=compiled.candidate_sha256,
                configuration_sha256=self.config,
                dataset=self.dataset,
                split=self.split,
                provider=self.provider,
                budget=self.budget(),
                per_call=self.per_call,
                check_use=lambda: self.guard("evaluation"),
                settings=self.settings,
            )
        self.commit_evaluated(report)
        return report
