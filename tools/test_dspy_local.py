#!/usr/bin/env python3
"""Separately runnable real pinned DSPy/durable evaluation/local serving lane."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def check(value, message):
    if not value:
        raise AssertionError(message)


def run():
    # This entrypoint runs inside the no-network container after file verification.
    check(sys.version_info[:2] == (3, 14), "unqualified Python")
    check(
        sys.platform == "linux" and os.uname().machine == "aarch64",
        "unqualified target",
    )

    def denied(*args, **kwargs):
        raise AssertionError("outbound network forbidden")

    socket.create_connection = denied
    socket.socket.connect = denied
    from refinery.storage import Store, VERSION, pack
    from refinery.durable_evaluation import run as evaluate
    from refinery.dspy_local import LocalDSPyProvider
    from refinery.evaluation import VirtualClock
    from refinery.serving import Registry, Policy, Approval, LocalServing
    from dataclasses import replace
    from test_durable import setup

    inventory = ROOT / "offline/dependencies.json"
    dependency_sha = hashlib.sha256(inventory.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="dspy-local-") as temporary:
        directory = Path(temporary)
        with Store(directory / "refinery.sqlite") as store:
            snapshot, exp, script = setup(store)
            provider = LocalDSPyProvider(
                script.script, clock=VirtualClock(), dependency_sha256=dependency_sha
            )
            config = replace(
                exp.configuration,
                provider_script_sha256=provider.sha256,
                provider="dspy.local.v1",
                optimizer="dspy.predict.v1",
            )
            exp = replace(exp, configuration=config, config_sha256=config.sha256)
            report = evaluate(
                store,
                VERSION,
                "dspy-local",
                snapshot,
                exp,
                provider=provider,
                budget_id="shared",
            )
            check(
                report.status == "passed",
                "local DSPy evaluation failed: " + str(report.reason),
            )
            check(
                len(provider.calls) == len(provider.lm_calls) == len(report.attempts),
                "unbudgeted DSPy call",
            )
            reg = Registry(
                store,
                verify_approval=lambda approval: (
                    "synthetic-reviewer"
                    if approval.evidence == "independent-synthetic-proof"
                    else None
                ),
            )
            key = reg.qualify(
                VERSION, "dspy-local", Policy("disposable", 1_000_000, 0, 2000, 2000)
            )
            c = store.get("candidate", key)
            grant = Approval(
                c["candidate_sha256"],
                c["evaluation_sha256"],
                c["policy_sha256"],
                "disposable",
                "synthetic-reviewer",
                "independent-synthetic-proof",
            )
            adapter = LocalServing(directory / "serving.sqlite")
            try:
                reg.begin(VERSION, "local-load", key, grant, expected_generation=0)
                check(
                    reg.load(VERSION, "local-load", adapter) == "active",
                    "serving readback mismatch",
                )
                output = adapter.predict("disposable", "hello")
                check(
                    output["candidate_sha256"] == exp.candidate_sha256
                    and output["output"] == "answer",
                    "actual serving attribution mismatch",
                )
            finally:
                adapter.close()
            return {
                "status": "passed",
                "evidence_class": "isolated_dependency_compatibility",
                "dspy": "3.1.0",
                "calls": len(provider.calls),
                "lm_calls": len(provider.lm_calls),
                "report_sha256": hashlib.sha256(pack(report)).hexdigest(),
                "candidate_sha256": exp.candidate_sha256,
                "dependencies_sha256": dependency_sha,
                "quality": "synthetic_exact_match_only",
                "monetary_cost": "not_measured",
                "provider_live": "not_run",
            }


if __name__ == "__main__":
    first, second = run(), run()
    check(first == second, "fixed-seed normalized DSPy outcomes differ")
    print(json.dumps({**first, "repeat_runs": 2}, sort_keys=True))
