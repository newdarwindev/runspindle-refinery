"""Real pinned Tether/Refinery stores with synthetic use authorities and calls."""

from dataclasses import replace
import importlib.util
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, cast
import unittest
from unittest.mock import patch

from refinery import durable_evaluation as de
from refinery import durable_optimization as do
from refinery import evaluation as ev
from refinery import lineage as lin
from refinery import optimization as opt
from refinery.feedback_use import RetainedFeedbackUse, UseDenied
from refinery.storage import Store, VERSION, pack, unpack
import test_feedback_collection as collection_fixture

ROOT = Path(__file__).resolve().parents[1]
LIMITS = ev.Limits(20, 200, 2000, 200)
legacy: Any = ev
open_store: Any = Store
encode = cast(Callable[[Any], bytes], pack)
decode = cast(Callable[[bytes], dict[str, Any]], unpack)


class UseTests(unittest.TestCase):
    # Reuse the existing real component fixture, not its collection test methods.
    store: Any
    output: Any
    dep: Any
    feedback: Any
    target: Any
    material: Any
    delivery: Any
    payload: Any
    root: Path
    setUp: Any = collection_fixture.CollectionTests.setUp
    event: Any = collection_fixture.CollectionTests.event
    approve: Any = collection_fixture.CollectionTests.approve
    collect: Any = collection_fixture.CollectionTests.collect

    def prepare(
        self, purpose: str, *, attempts: int = 1, timeout: bool = False
    ) -> tuple[Any, ...]:
        self.allowed = True
        self.decisions: list[Any] = []
        seed = next(
            f"seed-{i}"
            for i in range(100)
            if (int(lin._json_digest([f"seed-{i}", "family"]), 16) % 10 < 2)
            == (purpose == "evaluation")
        )
        snapshot = self.collect(seed=seed)
        data, split = self.store.lineage(snapshot)
        self.assertEqual(len(data.examples), 1)
        script: tuple[ev.Completion, ...] = (
            ev.Completion(self.output.content, 1, 1, True, 1),
        ) * 2
        if timeout:
            script = (ev.Completion(None, 1, 1, True, 1, "timeout"),) + script
        provider = ev.ScriptedProvider(script, clock=legacy.VirtualClock())
        policy = RetainedFeedbackUse(
            self.dep,
            self.feedback,
            verify_material=lambda target, material: target == self.target
            and material == self.material,
            approve_use=lambda request: self.decision(request),
        )
        config = ev.Configuration(
            LIMITS, ev.Reservation(10, 10), provider.sha256, 1_000_000, 0, attempts, ()
        )
        experiment = ev.Experiment(
            split.dataset_sha256,
            split.sha256,
            b"baseline",
            lin.digest(b"baseline"),
            b"candidate",
            lin.digest(b"candidate"),
            config,
            config.sha256,
        )
        return snapshot, provider, policy, experiment

    def decision(self, request: Any) -> bool:
        self.decisions.append(request)
        return self.allowed

    def invoke(self, purpose: str, prepared: tuple[Any, ...], **changes: Any) -> Any:
        snapshot, provider, policy, experiment = prepared
        args = dict(provider=provider, budget_id="budget", retained_use=policy)
        args.update(changes)
        if purpose == "evaluation":
            return de.run(self.store, VERSION, "run", snapshot, experiment, **args)
        return do.run(
            self.store,
            VERSION,
            "run",
            snapshot,
            (b"candidate", b"alternative"),
            limits=LIMITS,
            per_call=ev.Reservation(10, 10),
            max_attempts=experiment.configuration.max_attempts,
            **args,
        )

    def mutate(self, kind: str) -> None:
        if kind == "revoke":
            self.allowed = False
            return
        raw = self.event(2, judgment="negative")
        if kind == "delete":
            raw = self.event(2, kind="delete", judgment=None)
        elif kind == "conflict":
            raw = self.event(judgment="negative")
        self.feedback.ingest(raw, lin.digest(raw), self.delivery, self.payload)

    def ledger(self) -> dict[str, Any]:
        return decode(
            self.store.db.execute("SELECT payload FROM budgets").fetchone()[0]
        )

    def fresh_case(self) -> Any:
        case = UseTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def assert_no_execution(self, provider: Any) -> None:
        self.assertEqual(provider.calls, [])
        for table in ("budgets", "experiments", "attempts"):
            self.assertEqual(
                self.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
            )

    def test_missing_false_raising_or_nonboolean_authority_never_writes_or_calls(
        self,
    ) -> None:
        for purpose in ("evaluation", "optimization"):
            case = self.fresh_case()
            ready = case.prepare(purpose)
            policy = ready[2]

            def bad(*_: Any) -> bool:
                raise RuntimeError("private canary")

            policies = [None]
            for field in ("verify_material", "approve_use"):
                for callback in (None, lambda *_: False, lambda *_: 1, bad):
                    policies.append(replace(policy, **{field: callback}))
            for invalid in policies:
                with (
                    self.subTest(purpose=purpose, policy=invalid),
                    self.assertRaisesRegex(UseDenied, "^retained_feedback_use_denied$"),
                ):
                    case.invoke(purpose, ready, retained_use=invalid)
                case.assert_no_execution(ready[1])
            with self.assertRaises(UseDenied):
                case.invoke(purpose, ready, retained_use=None, synthetic_fixture=True)
            case.assert_no_execution(ready[1])

    def test_edit_delete_conflict_and_revoke_deny_before_any_claim(self) -> None:
        for purpose in ("evaluation", "optimization"):
            for mutation in ("edit", "delete", "conflict", "revoke"):
                with self.subTest(purpose=purpose, mutation=mutation):
                    case = self.fresh_case()
                    ready = case.prepare(purpose)
                    history = case.store.db.execute(
                        "SELECT * FROM objects ORDER BY kind,identity"
                    ).fetchall()
                    case.mutate(mutation)
                    with self.assertRaises(UseDenied):
                        case.invoke(purpose, ready)
                    case.assert_no_execution(ready[1])
                    self.assertEqual(
                        case.store.db.execute(
                            "SELECT * FROM objects ORDER BY kind,identity"
                        ).fetchall(),
                        history,
                    )
                    self.assertEqual(
                        case.store.lineage(ready[0])[0].examples[0].gold,
                        case.output.content,
                    )

    def test_permission_is_bound_to_exact_configuration_and_run(self) -> None:
        for purpose in ("evaluation", "optimization"):
            case = self.fresh_case()
            ready = case.prepare(purpose)
            case.invoke(purpose, ready)
            approved = case.decisions[0]
            policy = replace(ready[2], approve_use=lambda request: request == approved)
            before = case.store.db.execute("SELECT * FROM budgets").fetchall()
            provider = ev.ScriptedProvider(ready[1].script, clock=legacy.VirtualClock())
            with self.assertRaises(UseDenied):
                case.invoke(
                    purpose,
                    ready,
                    provider=provider,
                    retained_use=policy,
                    budget_id="other",
                )
            config = replace(ready[3].configuration, max_attempts=2)
            changed = replace(
                ready[3], configuration=config, config_sha256=config.sha256
            )
            with self.assertRaises(UseDenied):
                case.invoke(
                    purpose,
                    (*ready[:3], changed),
                    provider=provider,
                    retained_use=policy,
                )
            self.assertEqual(provider.calls, [])
            self.assertEqual(
                case.store.db.execute("SELECT * FROM budgets").fetchall(), before
            )

    def test_absent_actual_evidence_and_dependency_substitution_deny(self) -> None:
        ready = self.prepare("evaluation")
        with (
            patch.object(
                type(self.feedback), "evidence", side_effect=ValueError("unavailable")
            ),
            self.assertRaises(UseDenied),
        ):
            self.invoke("evaluation", ready)
        self.assert_no_execution(ready[1])
        with (
            patch.dict(sys.modules, {"tether.feedback": object()}),
            self.assertRaises(UseDenied),
        ):
            self.invoke("evaluation", ready)
        self.assert_no_execution(ready[1])

    def test_mutating_authority_callbacks_require_final_tether_reread(self) -> None:
        for purpose in ("evaluation", "optimization"):
            for field in ("verify_material", "approve_use"):
                for mutation in ("edit", "delete", "conflict"):
                    with self.subTest(purpose=purpose, field=field, mutation=mutation):
                        case = self.fresh_case()
                        ready = case.prepare(purpose)

                        def approve(*_: Any) -> bool:
                            case.mutate(mutation)
                            return True

                        policy = replace(ready[2], **{field: approve})
                        with self.assertRaises(UseDenied):
                            case.invoke(purpose, ready, retained_use=policy)
                        case.assert_no_execution(ready[1])

    def test_revocation_after_reservation_preserves_charge_without_dispatch(
        self,
    ) -> None:
        for purpose in ("evaluation", "optimization"):
            for mutation in ("revoke", "edit", "delete", "conflict"):
                with self.subTest(purpose=purpose, mutation=mutation):
                    case = self.fresh_case()
                    ready = case.prepare(purpose)

                    def checkpoint(point: str) -> None:
                        if point == "after_budget_reservation":
                            case.mutate(mutation)

                    with self.assertRaises(UseDenied):
                        case.invoke(purpose, ready, checkpoint=checkpoint)
                    self.assertEqual(ready[1].calls, [])
                    ledger = case.ledger()
                    self.assertEqual(ledger["calls"], 1)
                    self.assertEqual(ledger["pending"], {"1": ev.Reservation(10, 10)})
                    self.assertEqual(ledger["reason"], "retained_feedback_use_denied")
                    self.assertEqual(
                        case.store.db.execute(
                            "SELECT outcome,receipt FROM attempts"
                        ).fetchall(),
                        [("unknown", None)],
                    )
                    self.assertEqual(
                        case.store.db.execute(
                            "SELECT state,report FROM experiments"
                        ).fetchall(),
                        [("running", None)],
                    )
                    case.allowed = True
                    with self.assertRaises(ev.Halt):
                        case.invoke(purpose, ready)
                    self.assertEqual(ready[1].calls, [])

    def test_between_calls_and_timeout_retries_recheck_without_new_reservation(
        self,
    ) -> None:
        for purpose in ("evaluation", "optimization"):
            for timeout in (False, True):
                case = self.fresh_case()
                ready = case.prepare(purpose, attempts=2, timeout=timeout)
                original = ev.ScriptedProvider.complete

                def dispatch(provider: Any, request: Any) -> Any:
                    reply = original(provider, request)
                    case.allowed = False
                    return reply

                with (
                    patch.object(ev.ScriptedProvider, "complete", dispatch),
                    self.assertRaises(UseDenied),
                ):
                    case.invoke(purpose, ready)
                self.assertEqual(len(ready[1].calls), 1)
                ledger = case.ledger()
                self.assertEqual(
                    (
                        ledger["calls"],
                        ledger["tokens"],
                        ledger["cost"],
                        ledger["pending"],
                    ),
                    (1, 1, 1, {}),
                )

    def test_commit_and_return_recheck_without_rewriting_history(self) -> None:
        for purpose in ("evaluation", "optimization"):
            phase = "report" if purpose == "evaluation" else "selection"
            for boundary in ("before", "after"):
                case = self.fresh_case()
                ready = case.prepare(purpose)

                def checkpoint(point: str) -> None:
                    if point == f"{boundary}_{phase}_commit":
                        case.allowed = False

                with self.assertRaises(UseDenied):
                    case.invoke(purpose, ready, checkpoint=checkpoint)
                self.assertEqual(len(ready[1].calls), 2)
                row = case.store.db.execute(
                    "SELECT state,report FROM experiments"
                ).fetchone()
                self.assertEqual(
                    row[0], "running" if boundary == "before" else "complete"
                )
                if boundary == "before":
                    self.assertIsNone(row[1])
                    self.assertEqual(
                        case.store.db.execute(
                            "SELECT count(*) FROM objects WHERE kind IN ('report','selection')"
                        ).fetchone()[0],
                        0,
                    )
                else:
                    history = case.store.db.execute(
                        "SELECT * FROM objects ORDER BY kind,identity"
                    ).fetchall()
                    with self.assertRaises(UseDenied):
                        case.invoke(purpose, ready)
                    self.assertEqual(
                        history,
                        case.store.db.execute(
                            "SELECT * FROM objects ORDER BY kind,identity"
                        ).fetchall(),
                    )

    def test_authorized_success_and_cached_reopen_require_current_exact_decision(
        self,
    ) -> None:
        for purpose in ("evaluation", "optimization"):
            case = self.fresh_case()
            ready = case.prepare(purpose)
            original_lineage = case.store.get("lineage", ready[0])
            result = case.invoke(purpose, ready)
            self.assertEqual(
                result.status, "passed" if purpose == "evaluation" else "selected"
            )
            self.assertEqual(len(ready[1].calls), 2)
            expected_partition = "heldout" if purpose == "evaluation" else "train"
            self.assertEqual(
                original_lineage["split"].partition("family"), expected_partition
            )
            requests = case.decisions
            self.assertGreater(len(requests), 6)
            self.assertEqual(len(set(requests)), 1)
            self.assertEqual(requests[0].purpose, purpose)
            self.assertEqual(requests[0].snapshot_sha256, ready[0])
            self.assertEqual(
                requests[0].configuration_sha256,
                ready[3].config_sha256
                if purpose == "evaluation"
                else result.configuration_sha256,
            )
            with open_store(case.root / "refinery.sqlite") as reopened:
                case.store = reopened
                fresh = ev.ScriptedProvider(
                    ready[1].script, clock=legacy.VirtualClock()
                )
                self.assertEqual(case.invoke(purpose, ready, provider=fresh), result)
                self.assertEqual(fresh.calls, [])
                self.assertEqual(case.store.get("lineage", ready[0]), original_lineage)
                # Revoke only during the cached-return check, after entry/claim checks.
                count = 0

                def approve(_: Any) -> bool:
                    nonlocal count
                    count += 1
                    return count < 3

                with self.assertRaises(UseDenied):
                    case.invoke(
                        purpose,
                        ready,
                        provider=fresh,
                        retained_use=replace(ready[2], approve_use=approve),
                    )
                self.assertEqual(count, 3)
                self.assertEqual(fresh.calls, [])
                case.mutate("delete")
                with self.assertRaises(UseDenied):
                    case.invoke(purpose, ready, provider=fresh)
                self.assertEqual(fresh.calls, [])

    def test_fresh_process_cannot_reuse_success_without_a_use_authority(self) -> None:
        ready = self.prepare("evaluation")
        self.invoke("evaluation", ready)
        program = """
import sys
from refinery import evaluation as ev
legacy = ev
from refinery.durable_evaluation import run
from refinery.storage import Store, VERSION
from refinery.feedback_use import UseDenied
with Store(sys.argv[1]) as store:
    experiment = store.get("experiment", "run")
    provider = ev.ScriptedProvider((), clock=legacy.VirtualClock())
    try:
        run(store, VERSION, "run", sys.argv[2], experiment, provider=provider, budget_id="budget")
    except UseDenied:
        print("denied", len(provider.calls))
    else:
        raise AssertionError("cached authorization bypass")
"""
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                str(self.root / "refinery.sqlite"),
                ready[0],
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "denied 0")

    def test_forged_receipt_batch_snapshot_and_material_associations_deny(self) -> None:
        ready = self.prepare("evaluation")
        original = self.store.get("lineage", ready[0])
        receipt = self.store.get("feedback_collection", original["collection_evidence"])
        mutations: list[dict[str, Any]] = [
            {"batch_sha256": "0" * 64},
            {"dependency_commit": "0" * 40},
            {"selected": ()},
            {"scope": "perpetual_use"},
        ]
        row = receipt["selected"][0]
        for field, value in (
            ("material_ref", "forged"),
            ("target", {}),
            ("evidence_sha256", "0" * 64),
            ("selection", {**row["selection"], "rule_id": "forged"}),
        ):
            mutations.append({"selected": ({**row, field: value},)})
        snapshots = [
            {**original, "collection_evidence": "0" * 64},
            {**original, "collection_evidence": "synthetic-consent"},
            {**original, "batch_sha256": "0" * 64},
        ]
        # A synthetic marker is not permission to reuse retained observations
        # under a new batch hash, split seed or rule inventory.
        sources, outputs, feedback, rules, seed = original["batch"]
        for changed_rules, changed_seed in (
            (rules, "new-seed"),
            ((replace(rules[0], kinds=("positive",)),), seed),
        ):
            batch = (sources, outputs, feedback, changed_rules, changed_seed)
            snapshots.append(
                {
                    **original,
                    "batch": batch,
                    "batch_sha256": lin.digest(encode(batch)),
                    "split": lin.lock_split(original["dataset"], seed=changed_seed),
                    "collection_evidence": "synthetic-consent",
                }
            )
        forged_data = replace(
            original["dataset"],
            examples=(replace(original["dataset"].examples[0], gold=b"forged-gold"),),
        )
        snapshots.append(
            {
                **original,
                "dataset": forged_data,
                "split": lin.lock_split(forged_data, seed=original["split"].seed),
            }
        )
        with self.store.transaction() as tx:
            for changes in mutations:
                bad = {**receipt, **changes}
                sha = lin.digest(encode(bad))
                self.store.put(tx, "feedback_collection", sha, bad)
                snapshots.append({**original, "collection_evidence": sha})
            sources, outputs, feedback, rules, seed = original["batch"]
            batch = (
                sources,
                outputs,
                (replace(feedback[0], actor_id="forged"),),
                rules,
                seed,
            )
            sha = lin.digest(encode(batch))
            bad = {**receipt, "batch_sha256": sha}
            reference = lin.digest(encode(bad))
            self.store.put(tx, "feedback_collection", reference, bad)
            snapshots.append(
                {
                    **original,
                    "batch": batch,
                    "batch_sha256": sha,
                    "collection_evidence": reference,
                }
            )
            for snapshot in snapshots:
                self.store.put(tx, "lineage", lin.digest(encode(snapshot)), snapshot)
        for snapshot in snapshots:
            sha = lin.digest(encode(snapshot))
            for synthetic in (False, True):
                with (
                    self.subTest(snapshot=sha, synthetic=synthetic),
                    self.assertRaises(UseDenied),
                ):
                    experiment = replace(
                        ready[3],
                        dataset_sha256=snapshot["split"].dataset_sha256,
                        split_sha256=snapshot["split"].sha256,
                    )
                    self.invoke(
                        "evaluation",
                        (sha, ready[1], ready[2], experiment),
                        synthetic_fixture=synthetic,
                        retained_use=None if synthetic else ready[2],
                    )
        self.assert_no_execution(ready[1])

    def test_cancellation_after_reservation_does_not_dispatch(self) -> None:
        for purpose in ("evaluation", "optimization"):
            case = self.fresh_case()
            ready = case.prepare(purpose)

            def checkpoint(point: str) -> None:
                if point == "after_budget_reservation":
                    ledger: Any = de.SQLiteBudget(
                        case.store, "budget", LIMITS, clock_ms=ready[1].clock
                    )
                    ledger.cancel()

            result = case.invoke(purpose, ready, checkpoint=checkpoint)
            self.assertEqual((result.status, result.reason), ("blocked", "cancelled"))
            self.assertEqual(ready[1].calls, [])
            self.assertEqual(case.ledger()["pending"], {"1": ev.Reservation(10, 10)})

    def test_removed_dispatch_guard_mutations_expose_independent_forbidden_calls(
        self,
    ) -> None:
        for purpose, name, module, target in (
            ("evaluation", "evaluation", ev, de),
            ("optimization", "optimization", opt, do),
        ):
            case = self.fresh_case()
            ready = case.prepare(purpose)
            source = (ROOT / "refinery" / (name + ".py")).read_text()
            lines = source.splitlines(keepends=True)
            anchors = [line for line in lines if "# dispatch guard" in line]
            self.assertEqual(len(anchors), 1)
            path = case.root / "mutant.py"
            path.write_text(source.replace(anchors[0], ""))
            spec = importlib.util.spec_from_file_location(
                "refinery.mutant_" + name, path
            )
            if spec is None or spec.loader is None:
                self.fail("mutation loader unavailable")
            mutant = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mutant
            self.addCleanup(sys.modules.pop, spec.name)
            spec.loader.exec_module(mutant)
            # Reuse actual dataclass identities and adapters, changing only the seam.
            for attribute, value in vars(module).items():
                if isinstance(value, type):
                    setattr(mutant, attribute, value)
            function = "evaluate" if purpose == "evaluation" else "optimize"

            def checkpoint(point: str) -> None:
                if point == "after_budget_reservation":
                    case.allowed = False

            with (
                patch.object(target, function, getattr(mutant, function)),
                self.assertRaises(UseDenied),
            ):
                case.invoke(purpose, ready, checkpoint=checkpoint)
            self.assertEqual(
                len(ready[1].calls),
                1,
                "mutant must expose the forbidden provider dispatch",
            )
            self.assertEqual(case.ledger()["calls"], 1)

    def test_standalone_fixture_opt_in_is_explicit(self) -> None:
        from test_durable import setup

        fixture: Any = setup
        snapshot, experiment, provider = fixture(self.store)
        with self.assertRaises(UseDenied):
            de.run(
                self.store,
                VERSION,
                "fixture",
                snapshot,
                experiment,
                provider=provider,
                budget_id="budget",
            )
        self.assert_no_execution(provider)
        result = de.run(
            self.store,
            VERSION,
            "fixture",
            snapshot,
            experiment,
            provider=provider,
            budget_id="budget",
            synthetic_fixture=True,
        )
        self.assertEqual(result.status, "passed")
