"""Actual isolated DSPy compile/evaluate/replay in three fresh interpreters.

No source-unit test may substitute this execution evidence. Run inside the
existing reviewed offline optimizer container, never against a live provider.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PHASES = ("compile", "evaluate", "replay")
IDENTITY = "fresh-process-dspy"
BUDGET = "shared-fresh-process"
SOURCES = (
    "tools/dspy_fresh_process.py",
    "tools/test_dspy_optimizer.py",
    "tools/offline_dspy.py",
    "refinery/dspy_optimizer.py",
    "refinery/durable_dspy.py",
    "refinery/durable_evaluation.py",
    "refinery/evaluation.py",
    "refinery/lineage.py",
    "refinery/optimization.py",
    "refinery/heldout_policy.py",
    "refinery/feedback_use.py",
    "refinery/storage.py",
    "tests/test_durable.py",
    "tests/test_lineage.py",
    "offline/dependencies.json",
    "offline/dependency-adoption.json",
    "offline/toolchain.json",
    "offline/requirements.lock",
)


def require(value: Any, message: str) -> None:
    if not value:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: digest((ROOT / name).read_bytes()) for name in SOURCES}


def validate_receipt(receipt: Any, phase: str) -> None:
    require(
        type(receipt) is dict and receipt.get("phase") == phase,
        "child phase receipt mismatch",
    )
    for key in (
        "snapshot",
        "candidate_sha256",
        "configuration_sha256",
        "binding_sha256",
        "compiled_sha256",
        "ledger_sha256",
    ):
        require(
            isinstance(receipt.get(key), str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt[key]),
            "invalid child digest: " + key,
        )
    for key in (
        "heldout_count",
        "new_dispatches",
        "persisted_calls",
        "forbidden_network_attempts",
        "clock_ms",
    ):
        require(
            type(receipt.get(key)) is int and 0 <= receipt[key] <= 4096,
            "invalid child count: " + key,
        )
    require(
        receipt["heldout_count"] > 0 and receipt.get("budget_id") == BUDGET,
        "invalid child fixture binding",
    )
    require(
        type(receipt.get("framework_loaded")) is bool, "invalid framework observation"
    )
    require(type(receipt.get("source_sha256")) is dict, "missing child source hashes")
    if phase == "compile":
        require(
            receipt.get("report_sha256") is None and receipt["framework_loaded"],
            "invalid compile outcome",
        )
    else:
        require(
            isinstance(receipt.get("report_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", receipt["report_sha256"]),
            "invalid result digest",
        )


def validate_transition(previous: dict[str, Any], current: dict[str, Any]) -> None:
    for key in (
        "snapshot",
        "candidate_sha256",
        "configuration_sha256",
        "binding_sha256",
        "budget_id",
        "source_sha256",
        "heldout_count",
        "compiled_sha256",
    ):
        require(
            current.get(key) == previous.get(key),
            "fresh-process binding changed: " + key,
        )
    require(current.get("forbidden_network_attempts") == 0, "forbidden network effect")
    require(
        current.get("resume_clock_ms") == previous.get("clock_ms"),
        "persisted virtual clock was not restored",
    )
    if current.get("phase") == "evaluate":
        require(previous.get("phase") == "compile", "invalid evaluation predecessor")
        require(
            current.get("new_dispatches") == 2 * current["heldout_count"],
            "evaluation replayed compilation or missed heldout dispatch",
        )
        require(
            current.get("persisted_calls")
            == previous["persisted_calls"] + current["new_dispatches"],
            "shared ledger dispatch mismatch",
        )
    elif current.get("phase") == "replay":
        require(previous.get("phase") == "evaluate", "invalid replay predecessor")
        require(current.get("new_dispatches") == 0, "completed result dispatched again")
        for key in ("report_sha256", "ledger_sha256", "persisted_calls"):
            require(
                current.get(key) == previous.get(key),
                "completed receipt changed: " + key,
            )
        require(
            current.get("framework_loaded") is False,
            "completed replay imported framework",
        )
    else:
        raise ValueError("invalid successor phase")


def child(phase: str, directory: Path) -> dict[str, Any]:
    require(phase in PHASES, "unknown child phase")
    require(sys.version_info[:3] == (3, 14, 0), "unqualified Python")
    require(
        sys.platform == "linux" and os.uname().machine == "aarch64",
        "unqualified target",
    )
    sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")]
    forbidden: list[str] = []

    def deny(*args: Any, **kwargs: Any) -> Any:
        forbidden.append("network_attempt")
        raise ValueError("outbound network forbidden")

    with (
        patch.object(socket, "create_connection", deny),
        patch.object(socket.socket, "connect", deny),
        patch.object(socket.socket, "connect_ex", deny),
        patch.object(socket.socket, "sendto", deny),
    ):
        # Network denial is active before importing any component/framework.
        verifier: Any = importlib.import_module("offline_dspy")
        declared, _ = verifier.reviewed_closure()
        spec = importlib.util.find_spec("dspy")
        require(
            spec is not None and spec.origin is not None, "prepared framework missing"
        )
        if spec is None or spec.origin is None:
            raise ValueError("prepared framework missing")
        require(
            verifier.inventory(Path(spec.origin).resolve().parents[1]) == declared,
            "prepared bytes changed",
        )
        ev: Any = importlib.import_module("refinery.evaluation")
        opt: Any = importlib.import_module("refinery.dspy_optimizer")
        storage: Any = importlib.import_module("refinery.storage")
        lifecycle: Any = importlib.import_module("refinery.durable_dspy")
        fixture: Any = importlib.import_module("test_durable")
        previous: Any = (
            None
            if phase == "compile"
            else json.loads((directory / "previous.json").read_bytes())
        )
        with storage.Store(directory / "store.sqlite") as store:
            if phase == "compile":
                snapshot, _, _ = fixture.setup(store)
            else:
                validate_receipt(
                    previous, "compile" if phase == "evaluate" else "evaluate"
                )
                snapshot = previous["snapshot"]
                require(
                    previous["source_sha256"] == source_hashes(),
                    "source changed between processes",
                )
            dataset, split = store.lineage(snapshot)
            train, heldout = opt.validate_dataset(dataset, split)
            script = (
                ev.Completion(b"wrong-first-round", 1, 1, True, 1),
                ev.Completion(train[0].gold, 1, 1, True, 1),
            ) + tuple(
                ev.Completion(e.gold, 1, 1, True, 1) for _ in range(2) for e in heldout
            )
            provider = ev.ScriptedProvider(script, clock=ev.VirtualClock())
            session = lifecycle.Session(
                store,
                storage.VERSION,
                IDENTITY,
                snapshot,
                provider=provider,
                limits=ev.Limits(100, 1000, 1000, 1000),
                per_call=ev.Reservation(10, 10),
                settings=opt.Settings(1, 2),
                budget_id=BUDGET,
                synthetic_fixture=True,
            )
            observed: list[Any] = []
            real_complete = ev.ScriptedProvider.complete

            def observe(instance: Any, request: Any) -> Any:
                observed.append(request)
                return real_complete(instance, request)

            resume_clock_ms = None
            if phase != "compile":
                compiled, clock_ms = session.compiled()
                ledger_bytes = store.db.execute(
                    "SELECT payload FROM budgets WHERE identity=?", (BUDGET,)
                ).fetchone()[0]
                ledger = storage.unpack(ledger_bytes)
                require(
                    digest(ledger_bytes) == previous["ledger_sha256"],
                    "persisted ledger changed between phases",
                )
                require(
                    digest(storage.pack(compiled)) == previous["compiled_sha256"],
                    "persisted compiled checkpoint changed",
                )
                # Restoring the cursor is not dispatch. Restore time independently
                # from verified persisted state before claim_evaluation().
                provider.clock.now = max(clock_ms, ledger["last"])
                resume_clock_ms = provider.clock.now
            with (
                patch.object(ev.ScriptedProvider, "complete", observe),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                if phase == "compile":
                    compiled = session.compile()
                    require(
                        compiled.status == "compiled",
                        "actual framework compilation failed",
                    )
                    require(len(observed) == 2, "actual compilation retry/copy missing")
                    require(
                        all(
                            r.stage == "optimization"
                            and r.output_id == train[0].output.output_id
                            for r in observed
                        ),
                        "compile dispatched heldout identity",
                    )
                    report = None
                elif phase == "evaluate":
                    report = session.evaluate()
                    require(
                        report["status"] == "evaluated",
                        "actual heldout evaluation failed",
                    )
                    expected = {
                        (stage, e.output.output_id, e.output.revision)
                        for stage in ("baseline", "candidate")
                        for e in heldout
                    }
                    require(
                        len(observed) == len(expected)
                        and {
                            (r.stage, r.output_id, r.output_revision) for r in observed
                        }
                        == expected,
                        "unexpected evaluation dispatch",
                    )
                else:
                    require(
                        "dspy" not in sys.modules, "replay framework already imported"
                    )
                    with patch.object(
                        opt,
                        "framework",
                        side_effect=ValueError("replay framework forbidden"),
                    ) as framework:
                        report = session.evaluate()
                    require(
                        framework.call_count == 0 and not observed,
                        "replay caused framework or dispatch effect",
                    )
                    require("dspy" not in sys.modules, "replay imported framework")
            compiled, _ = session.compiled()
            ledger_bytes = store.db.execute(
                "SELECT payload FROM budgets WHERE identity=?", (BUDGET,)
            ).fetchone()[0]
            ledger = storage.unpack(ledger_bytes)
            require(not forbidden, "forbidden network effect attempted")
            result = {
                "phase": phase,
                "snapshot": snapshot,
                "candidate_sha256": compiled.candidate_sha256,
                "configuration_sha256": session.config,
                "binding_sha256": session.binding_sha256,
                "compiled_sha256": digest(storage.pack(compiled)),
                "budget_id": BUDGET,
                "heldout_count": len(heldout),
                "new_dispatches": len(observed),
                "persisted_calls": ledger["calls"],
                "clock_ms": provider.clock(),
                "resume_clock_ms": resume_clock_ms,
                "ledger_sha256": digest(ledger_bytes),
                "report_sha256": None
                if report is None
                else digest(storage.pack(report)),
                "source_sha256": source_hashes(),
                "forbidden_network_attempts": len(forbidden),
                "framework_loaded": "dspy" in sys.modules,
                "_pid": os.getpid(),
            }
            if previous is not None:
                validate_transition(previous, result)
            return result


def child_environment(directory: Path) -> dict[str, str]:
    # Deliberately do not inherit account credentials or arbitrary host settings.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "PYTHONPATH": os.pathsep.join(
            ("/deps", str(ROOT), str(ROOT / "tests"), str(ROOT / "tools"))
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "HF_HUB_OFFLINE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "DSPY_CACHEDIR": str(directory / "cache"),
    }


def run() -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    pids = {os.getpid()}
    with tempfile.TemporaryDirectory(prefix="dspy-fresh-process-") as temporary:
        directory = Path(temporary)
        for phase in PHASES:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                try:
                    completed = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(ROOT / "tools/dspy_fresh_process.py"),
                            "--phase",
                            phase,
                            "--directory",
                            str(directory),
                        ],
                        cwd=ROOT,
                        env=child_environment(directory),
                        stdout=out,
                        stderr=err,
                        timeout=25,
                        check=False,
                    )
                except (subprocess.TimeoutExpired, OSError) as error:
                    raise ValueError(
                        "fresh child failed or timed out: " + phase
                    ) from error
                out.seek(0)
                payload = out.read(1024 * 1024 + 1)
            require(completed.returncode == 0, "fresh child failed: " + phase)
            require(len(payload) <= 1024 * 1024, "child receipt size limit")
            current = json.loads(payload)
            validate_receipt(current, phase)
            pid = current.pop("_pid", None)
            require(
                type(pid) is int and pid not in pids, "child was not a fresh process"
            )
            pids.add(pid)
            require(
                current["source_sha256"] == source_hashes(),
                "child source receipt mismatch",
            )
            require(
                current["forbidden_network_attempts"] == 0, "child attempted network"
            )
            if reports:
                validate_transition(reports[-1], current)
            else:
                require(
                    current["new_dispatches"] == current["persisted_calls"] == 2,
                    "compile receipt mismatch",
                )
            reports.append(current)
            (directory / "previous.json").write_text(
                json.dumps(current, sort_keys=True)
            )
    return {
        "status": "passed",
        "evidence_class": "actual_dspy_fresh_process_lifecycle",
        "fresh_interpreters": 3,
        "phases": reports,
        "live_provider": "not_run",
        "promotion": "not_authorized",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(child(args.phase, args.directory), sort_keys=True))
