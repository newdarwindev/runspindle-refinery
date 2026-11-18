"""Compile-only lifecycle fixtures, not actual GEPA framework qualification."""

from __future__ import annotations

import importlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from refinery import evaluation as ev
from refinery import gepa_codec as codec
from refinery import gepa_optimizer as gepa
from refinery.dspy_optimizer import case
from refinery.durable_dspy import Session
from refinery.gepa_evidence import GEPACompiled, validate_history
from refinery.lineage import digest

storage: Any = importlib.import_module("refinery.storage")
fixture: Any = importlib.import_module("test_durable")


class DurableGEPATests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = storage.Store(Path(temp.name) / "store.sqlite")
        self.addCleanup(self.store.close)
        self.snapshot, _, _ = fixture.setup(self.store)
        dataset, split = self.store.lineage(self.snapshot)
        self.example = next(
            e
            for e in dataset.examples
            if split.partition(e.source.family_id) == "train"
        )
        self.script: tuple[ev.Completion, ...] = (
            ev.Completion(b"wrong", 1, 1, True, 0),
            ev.Completion(b"Read carefully and answer exactly.", 1, 1, True, 0),
            ev.Completion(self.example.gold, 1, 1, True, 0),
        )
        self.session = self.make()

    def make(self, *, authority: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        module: Any = importlib.import_module("refinery.durable_dspy")
        original = module.use_guard

        def constructor_guard(*args: Any, **options: Any) -> Any:
            default = original(*args, **options)
            return (authority or {}).get(options["purpose"], default)

        # Synthetic constructor injection only; no production validator API or reseal.
        with patch.object(module, "use_guard", side_effect=constructor_guard):
            return Session(
                self.store,
                storage.VERSION,
                "gepa-run",
                self.snapshot,
                provider=ev.ScriptedProvider(self.script, clock=ev.VirtualClock()),
                limits=ev.Limits(16, 100, 100, 100),
                per_call=ev.Reservation(10, 10),
                budget_id="shared",
                strategy="gepa",
                training_output_id=self.example.output.output_id,
                synthetic_fixture=True,
                **kwargs,
            )

    def compile_fixture(self) -> Any:
        s = self.session
        s.claim_compile()
        d = gepa.Transport(
            self.example,
            s.split,
            config=s.config,
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("optimization"),
        )
        d.invoke_sealed(
            lambda **_: d.forward([]), "optimization", codec.INITIAL, case(self.example)
        )

        def reflection(**_: Any) -> Any:
            output = d.forward([]).choices[0].message.content
            return SimpleNamespace(instruction=output.split("\n")[1])

        d.propose(
            reflection,
            candidate={"predict": codec.INITIAL},
            components_to_update=["predict"],
            reflective_dataset={
                "predict": [
                    {
                        "Inputs": {"case": case(self.example)},
                        "Generated Outputs": {"answer": "wrong"},
                        "Feedback": "incorrect",
                    }
                ]
            },
        )
        instruction, payload, attempt = d.proposals[0]
        d.invoke_sealed(
            lambda **_: d.forward([]), "optimization", instruction, case(self.example)
        )
        candidate = codec.encode(
            instruction,
            payload,
            attempt,
            s.dataset,
            s.split,
            s.config,
            tuple(d.attempts),
        )
        result = GEPACompiled(
            "compiled",
            None,
            s.config,
            candidate,
            digest(candidate),
            tuple(d.attempts),
            tuple(sorted(d.budget.snapshot().items())),
        )
        s.commit_compiled(result)
        return result

    def test_compile_checkpoint_replays_without_framework_or_effects(self) -> None:
        expected = self.compile_fixture()
        resumed = self.make()
        with (
            patch.object(
                gepa, "runtime_identity", side_effect=AssertionError("runtime import")
            ),
            patch.object(gepa, "framework", side_effect=AssertionError("framework")),
        ):
            self.assertEqual(resumed.compile(), expected)
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(resumed.budget().snapshot()["calls"], 3)
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='gepa_request_witness'"
            ).fetchone()[0],
            3,
        )

    def test_unknown_provider_checkpoint_preserves_charge_and_replays(self) -> None:
        self.script = ()
        s = self.make()
        s.claim_compile()
        d = gepa.Transport(
            self.example,
            s.split,
            config=s.config,
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("optimization"),
        )
        with self.assertRaisesRegex(ev.Halt, "provider_unknown"):
            d.invoke_sealed(
                lambda **_: d.forward([]),
                "optimization",
                codec.INITIAL,
                case(self.example),
            )
        result = GEPACompiled(
            "blocked",
            "provider_unknown",
            s.config,
            None,
            None,
            tuple(d.attempts),
            tuple(sorted(d.budget.snapshot().items())),
        )
        s.commit_compiled(result)
        rows = tuple(self.store.db.execute("SELECT * FROM attempts"))
        ledger = tuple(self.store.db.execute("SELECT * FROM budgets"))
        resumed = self.make()
        with patch.object(
            gepa, "runtime_identity", side_effect=AssertionError("runtime")
        ):
            self.assertEqual(resumed.compile(), result)
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(tuple(self.store.db.execute("SELECT * FROM attempts")), rows)
        self.assertEqual(tuple(self.store.db.execute("SELECT * FROM budgets")), ledger)
        self.assertEqual(resumed.budget().snapshot()["unresolved_calls"], 1)

    def test_bootstrap_bytes_and_authority_match_frozen_parent(self) -> None:
        legacy: Any = importlib.import_module("test_durable_dspy")
        with (
            tempfile.TemporaryDirectory() as directory,
            storage.Store(Path(directory) / "golden.sqlite") as store,
        ):
            session = legacy.prepare(store)
            result = legacy.compile_fixture(session)
        # Captured from frozen PR80 2bcf357, not regenerated by the implementation.
        self.assertEqual(
            session.key,
            "dspy-b95b0207cc546f4bd3a6372f2b512cce870cadc4c26f349480ff4af61a8bd1a9",
        )
        self.assertEqual(
            session.config,
            "84f2504695f6d55bc242aa424c25185fa91975b0fe5019dd2ea0b51db2805e61",
        )
        self.assertEqual(
            session.binding.hex(),
            "7b227475706c65223a5b22726566696e6572792e647370792e6c6966656379636c652e7631222c2265346538376536653566333264386332343639366561383932363636326232393331313739656163393265356237333235666533336630336338643333633030222c2238346632353034363935663664353562633234326161343234633235313835666139313937356230666535303139646432656130623531646232383035653631222c22736861726564225d7d",
        )
        self.assertEqual(
            digest(storage.pack(result)),
            "68c29eba746b65ef24f61f9a8a0ed0ac282dece5db7dbe3ea23fedfd971e6fa1",
        )
        self.assertEqual(sorted(session.guards), ["evaluation", "optimization"])

    def test_recomputed_digest_cannot_authorize_foreign_instruction_or_payload(
        self,
    ) -> None:
        self.compile_fixture()
        s = self.session
        rows = s.receipts({"optimization", "reflection"})
        tickets = self.store.db.execute(
            "SELECT ticket FROM attempts WHERE experiment=? ORDER BY ticket", (s.key,)
        ).fetchall()
        witnesses = tuple(
            self.store.get("gepa_request_witness", s.key + ":" + str(t[0]))
            for t in tickets
        )
        for index, instruction, payload in (
            (2, "unapproved instruction", case(self.example)),
            (1, codec.INITIAL, "invented proposal"),
        ):
            with self.subTest(index=index):
                request, outcome, receipt = rows[index]
                request = replace(
                    request,
                    artifact_sha256=codec.association(
                        s.config, request.stage, instruction, payload
                    ),
                )
                altered_rows = list(rows)
                altered_rows[index] = (
                    request,
                    outcome,
                    replace(receipt, request=request),
                )
                altered_witnesses = list(witnesses)
                altered_witnesses[index] = (request, instruction, payload)
                with self.assertRaises(ValueError):
                    validate_history(
                        tuple(altered_rows),
                        tuple(altered_witnesses),
                        self.example,
                        s.split,
                        s.config,
                        self.script,
                    )
        with self.assertRaisesRegex(ValueError, "terminal"):
            validate_history(
                ((rows[0][0], "unknown", None),) + rows[1:],
                witnesses,
                self.example,
                s.split,
                s.config,
                self.script,
            )
        with self.assertRaisesRegex(ValueError, "count"):
            validate_history(
                rows,
                witnesses + (witnesses[0],),
                self.example,
                s.split,
                s.config,
                self.script,
            )

    def test_witness_write_failure_rolls_back_reservation_without_dispatch(
        self,
    ) -> None:
        s = self.session
        s.claim_compile()
        d = gepa.Transport(
            self.example,
            s.split,
            config=s.config,
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("optimization"),
        )
        ledger = tuple(self.store.db.execute("SELECT * FROM budgets"))
        with patch.object(
            storage.Store, "put", side_effect=ValueError("witness write failed")
        ):
            with self.assertRaisesRegex(ValueError, "witness write failed"):
                d.invoke_sealed(
                    lambda **_: d.forward([]),
                    "optimization",
                    codec.INITIAL,
                    case(self.example),
                )
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(tuple(self.store.db.execute("SELECT * FROM budgets")), ledger)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0
        )

    def test_same_name_unregistered_dataclass_is_not_storable(self) -> None:
        from dataclasses import make_dataclass

        fake = make_dataclass("GEPACompiled", [("status", str)])
        with self.assertRaisesRegex(ValueError, "unsupported stored type"):
            storage.pack(fake("compiled"))

    def test_changed_runtime_identity_or_script_cannot_recover(self) -> None:
        self.compile_fixture()
        from refinery import gepa_runtime

        with patch.object(gepa_runtime, "source_identity", return_value="f" * 64):
            with self.assertRaisesRegex(ValueError, "lifecycle conflict"):
                self.make().compile()
        self.script = (ev.Completion(b"different", 1, 1, True, 0),) + self.script[1:]
        with self.assertRaisesRegex(ValueError, "lifecycle conflict"):
            self.make().compile()

    def test_replay_still_requires_reflection_authority(self) -> None:
        self.compile_fixture()
        resumed = self.make()

        def deny() -> None:
            raise ev.Halt("reflection_revoked")

        resumed = self.make(authority={"reflection": deny})
        before = tuple(self.store.db.execute("SELECT * FROM budgets"))
        with self.assertRaisesRegex(ev.Halt, "reflection_revoked"):
            resumed.compile()
        self.assertEqual(resumed.provider.calls, [])
        self.assertEqual(tuple(self.store.db.execute("SELECT * FROM budgets")), before)

    def test_reflection_authority_callback_cannot_replace_clock(self) -> None:
        s = self.session

        def change() -> None:
            s.provider.clock = ev.VirtualClock()

        s = self.make(authority={"reflection": change})
        with self.assertRaisesRegex(ev.Halt, "provider_changed"):
            s.claim_compile()
        self.assertIsNone(s.row(self.store.db))
        self.assertEqual(s.provider.calls, [])

    def test_strategy_metadata_mutation_cannot_skip_reflection_before_claim(
        self,
    ) -> None:
        for field, value in (
            ("strategy", "other"),
            ("strategy", "bootstrap"),
            ("lifecycle", "foreign"),
            ("compiled_kind", "dspy_compiled"),
            ("training_output_id", "foreign"),
            ("key", "gepa-foreign"),
        ):
            with self.subTest(field=field, value=value):
                s = self.make()
                checked: list[str] = []

                def mutate() -> None:
                    setattr(s, field, value)

                def denied_reflection() -> None:
                    checked.append("reflection")
                    raise ev.Halt("reflection_denied")

                s = self.make(
                    authority={"optimization": mutate, "reflection": denied_reflection}
                )
                with self.assertRaisesRegex(ev.Halt, "strategy_changed"):
                    s.claim_compile()
                self.assertEqual(s.provider.calls, [])
                self.assertEqual(checked, [])
                self.assertEqual(
                    self.store.db.execute(
                        "SELECT count(*) FROM experiments"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[
                        0
                    ],
                    0,
                )

    def test_strategy_mutation_after_reservation_denies_provider_with_charge(
        self,
    ) -> None:
        s = self.session

        def mutate(stage: str) -> None:
            if stage == "after_budget_reservation":
                s.strategy = "other"

        s.checkpoint = mutate
        s.claim_compile()
        d = gepa.Transport(
            self.example,
            s.split,
            config=s.config,
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("optimization"),
        )
        with self.assertRaisesRegex(ev.Halt, "strategy_changed"):
            d.invoke_sealed(
                lambda **_: d.forward([]),
                "optimization",
                codec.INITIAL,
                case(self.example),
            )
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 1)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1
        )

    def test_authority_callback_cannot_replace_reflection_or_reseal_selection(
        self,
    ) -> None:
        s = self.session

        def replacement() -> None:
            s.guards["reflection"] = lambda: None

        s = self.make(authority={"optimization": replacement})
        with self.assertRaisesRegex(ev.Halt, "(strategy|authority)_changed"):
            s.claim_compile()
        self.assertEqual(s.provider.calls, [])
        self.assertIsNone(s.row(self.store.db))
        s = self.make()

        def reseal() -> None:
            s.training_output_id = "foreign"
            s._strategy_seal = Session.strategy_binding(s)
            s.strategy_binding = lambda: s._strategy_seal

        s = self.make(authority={"optimization": reseal})
        with self.assertRaisesRegex(ev.Halt, "(strategy|authority)_changed"):
            s.claim_compile()
        self.assertEqual(s.provider.calls, [])
        self.assertIsNone(s.row(self.store.db))

    def test_revocation_and_route_swap_between_checks_denies_effects(self) -> None:
        state = {"revoked": False, "checks_after_revocation": 0}

        def original_reflection() -> None:
            if state["revoked"]:
                state["checks_after_revocation"] += 1
                raise ev.Halt("reflection_revoked")

        def checkpoint(stage: str) -> None:
            if stage == "after_budget_reservation":
                state["revoked"] = True
                s.guards["reflection"] = lambda: None
                # A writable instance attribute or shadowed helper cannot reseal routing.
                s._gepa_authority_routes = tuple(s.guards.items())
                s.verify_authority_routes = lambda: ()

        s = self.make(
            authority={"reflection": original_reflection}, checkpoint=checkpoint
        )
        self.session = s
        with self.assertRaisesRegex(ev.Halt, "authority_changed"):
            self.compile_fixture()
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 1)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM attempts").fetchone()[0], 1
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='gepa_compiled'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(s.row(self.store.db)[1], "compiling")

    def test_revocation_without_route_swap_is_checked_after_reservation(self) -> None:
        state = {"revoked": False, "checks_after_revocation": 0}

        def original_reflection() -> None:
            if state["revoked"]:
                state["checks_after_revocation"] += 1
                raise ev.Halt("reflection_revoked")

        def checkpoint(stage: str) -> None:
            if stage == "after_budget_reservation":
                state["revoked"] = True

        s = self.make(
            authority={"reflection": original_reflection}, checkpoint=checkpoint
        )
        self.session = s
        with self.assertRaisesRegex(ev.Halt, "reflection_revoked"):
            self.compile_fixture()
        self.assertEqual(state["checks_after_revocation"], 1)
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 1)

    def test_missing_witness_rejects_replay(self) -> None:
        self.compile_fixture()
        self.store.db.execute("DROP TRIGGER objects_DELETE")
        self.store.db.execute("DELETE FROM objects WHERE kind='gepa_request_witness'")
        self.store.db.execute(
            "CREATE TRIGGER objects_DELETE BEFORE DELETE ON objects BEGIN SELECT RAISE(ABORT,'immutable retained record'); END"
        )
        with self.assertRaises((ValueError, KeyError)):
            self.make().compile()

    def test_changed_payload_rejects_even_rehashed_object(self) -> None:
        self.compile_fixture()
        kind, identity, raw = self.store.db.execute(
            "SELECT kind,identity,payload FROM objects WHERE kind='gepa_request_witness' ORDER BY identity LIMIT 1"
        ).fetchone()
        request, instruction, payload = storage.unpack(raw)
        data = storage.pack((request, instruction, payload + " altered"))
        self.store.db.execute("DROP TRIGGER objects_UPDATE")
        self.store.db.execute(
            "UPDATE objects SET payload=?,digest=? WHERE kind=? AND identity=?",
            (data, digest(data), kind, identity),
        )
        self.store.db.execute(
            "CREATE TRIGGER objects_UPDATE BEFORE UPDATE ON objects BEGIN SELECT RAISE(ABORT,'immutable retained record'); END"
        )
        with self.assertRaisesRegex(ValueError, "association"):
            self.make().compile()

    def test_reflection_authority_required_and_unowned_evaluation_denied(self) -> None:
        def deny() -> None:
            raise ev.Halt("reflection_denied")

        self.session = self.make(authority={"reflection": deny})
        with self.assertRaisesRegex(ev.Halt, "reflection_denied"):
            self.session.claim_compile()
        self.assertIsNone(self.session.row(self.store.db))
        with patch.object(gepa, "framework", side_effect=AssertionError("framework")):
            with self.assertRaisesRegex(ev.Halt, "interrupted_or_owned"):
                self.session.evaluate()

    def test_interrupted_reservation_retains_witness_and_charge(self) -> None:
        def interrupt(stage: str) -> None:
            if stage == "after_budget_reservation":
                raise RuntimeError("interrupted")

        s = self.make(checkpoint=interrupt)
        s.claim_compile()
        d = gepa.Transport(
            self.example,
            s.split,
            config=s.config,
            provider=s.provider,
            budget=s.budget(),
            per_call=s.per_call,
            check_use=lambda: s.guard("optimization"),
        )
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            d.invoke_sealed(
                lambda **_: d.forward([]),
                "optimization",
                codec.INITIAL,
                case(self.example),
            )
        self.assertEqual(s.provider.calls, [])
        self.assertEqual(s.budget().snapshot()["unresolved_calls"], 1)
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='gepa_request_witness'"
            ).fetchone()[0],
            1,
        )
        with self.assertRaisesRegex(ev.Halt, "interrupted_or_owned"):
            self.make().compile()
