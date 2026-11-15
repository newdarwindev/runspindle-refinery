#!/usr/bin/env python3
"""Real framework qualification. Run only through the reviewed isolated lane.

No skipped test or fake DSPy module can qualify this command. The normal Python
unit suite tests the boundary component separately without importing DSPy.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def check(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def run() -> dict[str, Any]:
    check(sys.version_info[:2] == (3, 14), "unqualified Python")
    check(
        sys.platform == "linux" and os.uname().machine == "aarch64",
        "unqualified target",
    )
    spec = importlib.util.find_spec("dspy")
    check(spec is not None and spec.origin is not None, "prepared DSPy missing")
    if spec is None or spec.origin is None:
        raise RuntimeError("prepared DSPy missing")
    verifier: Any = importlib.import_module("offline_dspy")
    check(
        verifier.inventory(Path(spec.origin).resolve().parents[1])
        == json.loads((ROOT / "offline/dependencies.json").read_bytes()),
        "dependency bytes changed",
    )
    forbidden: list[str] = []

    def denied(*args: Any, **kwargs: Any) -> Any:
        forbidden.append("network_attempt")
        raise AssertionError("outbound network forbidden")

    from unittest.mock import patch

    with (
        patch.object(socket, "create_connection", denied),
        patch.object(socket.socket, "connect", denied),
    ):
        optimizer: Any = importlib.import_module("refinery.dspy_optimizer")
        ev: Any = importlib.import_module("refinery.evaluation")
        storage: Any = importlib.import_module("refinery.storage")
        durable: Any = importlib.import_module("refinery.durable_evaluation")
        fixture: Any = importlib.import_module("test_durable")
        with tempfile.TemporaryDirectory(prefix="dspy-optimizer-") as directory:
            with storage.Store(Path(directory) / "store.sqlite") as store:
                snapshot, _, _ = fixture.setup(store)
                dataset, split = store.lineage(snapshot)
                train = tuple(
                    e
                    for e in dataset.examples
                    if split.partition(e.source.family_id) == "train"
                )
                heldout = tuple(
                    e
                    for e in dataset.examples
                    if split.partition(e.source.family_id) == "heldout"
                )
                script = (
                    ev.Completion(b"wrong-first-round", 1, 1, True, 1),
                    ev.Completion(train[0].gold, 1, 1, True, 1),
                ) + tuple(
                    ev.Completion(e.gold, 1, 1, True, 1)
                    for _ in range(2)
                    for e in heldout
                )
                clock = ev.VirtualClock()
                provider = ev.ScriptedProvider(script, clock=clock)
                budget = durable.SQLiteBudget(
                    store,
                    "shared",
                    ev.Limits(100, 1000, 1000, 1000),
                    clock_ms=clock,
                    experiment="dspy-bootstrap",
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    result = optimizer.optimize(
                        dataset,
                        split,
                        provider=provider,
                        budget=budget,
                        per_call=ev.Reservation(10, 10),
                        settings=optimizer.Settings(1, 2),
                        check_use=lambda: None,
                    )
                check(
                    result.status == "compiled", "real BootstrapFewShot did not compile"
                )
                check(len(result.attempts) == 2, "retry/copy accounting mismatch")
                check(
                    all(
                        r.stage == "optimization"
                        and r.output_id == train[0].output.output_id
                        for r in provider.calls
                    ),
                    "training dispatch leaked heldout identity",
                )
                candidate_digest = result.candidate_sha256
                evaluated = optimizer.evaluate_locked(
                    result.candidate,
                    expected_sha256=candidate_digest,
                    configuration_sha256=result.configuration_sha256,
                    settings=optimizer.Settings(1, 2),
                    dataset=dataset,
                    split=split,
                    provider=provider,
                    budget=budget,
                    per_call=ev.Reservation(10, 10),
                    check_use=lambda: None,
                )
                check(
                    evaluated["status"] == "evaluated",
                    "fixed heldout evaluation failed",
                )
                check(
                    evaluated["candidate_sha256"] == candidate_digest,
                    "candidate changed after compilation",
                )
                check(
                    evaluated["scores"]
                    == {"baseline": len(heldout), "candidate": len(heldout)},
                    "synthetic scores changed",
                )
                check(
                    len(provider.calls) == 2 + 2 * len(heldout),
                    "unaccounted framework call",
                )
                check(
                    budget.snapshot()["calls"] == len(provider.calls),
                    "shared ledger mismatch",
                )
                check(
                    all(
                        r.output_id in {e.output.output_id for e in heldout}
                        for r in provider.calls[2:]
                    ),
                    "heldout evaluation partition changed",
                )
                check(
                    store.db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE outcome='settled'"
                    ).fetchone()[0]
                    == len(provider.calls),
                    "durable receipts missing",
                )
                check(not forbidden, "forbidden network effect attempted")
                return {
                    "status": "passed",
                    "evidence_class": "isolated_offline_dspy_optimizer_component",
                    "optimizer": "dspy.BootstrapFewShot.compile",
                    "dspy": "3.1.0",
                    "candidate_sha256": candidate_digest,
                    "configuration_sha256": result.configuration_sha256,
                    "heldout_report_sha256": optimizer.digest(storage.pack(evaluated)),
                    "framework_calls": len(provider.calls),
                    "optimization_calls": 2,
                    "forbidden_network_attempts": len(forbidden),
                    "quality": "synthetic_exact_match_only",
                    "live_provider": "not_run",
                    "durable_run_recovery": "not_implemented",
                    "promotion": "not_authorized",
                    "source_sha256": {
                        p: optimizer.digest((ROOT / p).read_bytes())
                        for p in (
                            "refinery/dspy_optimizer.py",
                            "tests/test_durable.py",
                            "tests/test_lineage.py",
                            "offline/dependencies.json",
                            "offline/toolchain.json",
                            "offline/requirements.lock",
                        )
                    },
                }


if __name__ == "__main__":
    first, second = run(), run()
    check(first == second, "fixed inputs produced different artifacts")
    print(json.dumps({**first, "repeat_runs": 2}, sort_keys=True))
