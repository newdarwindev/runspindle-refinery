#!/usr/bin/env python3
"""Isolated behavioral mutations must fail assertions, not imports or syntax."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "feedback_rebinding": (
        "storage.py",
        "observations = lin._bounded(feedback + prior_conflicts, lin.Feedback)",
        "observations = lin._bounded(feedback, lin.Feedback)",
        "test_durable.DurableTests.test_feedback_id_cannot_rebind_across_committed_batches",
    ),
    "budget_reset": (
        "durable_evaluation.py",
        "budget = _restore(unpack(row[0]), self.clock_ms)",
        "budget = Budget(self.limits, clock_ms=self.clock_ms)",
        "test_durable.DurableTests.test_multi_connection_budget_concurrency_and_no_reset",
    ),
    "approval_bypass": (
        "serving.py",
        "if actor != approval.actor:",
        "if False:",
        "test_durable.ServingTests.test_exact_approval_default_deny_forgery_and_changed_target_zero_loads",
    ),
    "serving_attribution": (
        "serving.py",
        'return {"output": value["responses"][text], "candidate_sha256": row[0]}',
        'return {"output": value["responses"][text], "candidate_sha256": "0" * 64}',
        "test_durable.ServingTests.test_load_readback_actual_output_and_failed_load_preserves_prior",
    ),
}
results = []
for name, (filename, before, after, test) in MUTATIONS.items():
    with tempfile.TemporaryDirectory(prefix="refinery-durable-mutation-") as temporary:
        target = Path(temporary)
        for folder in ("refinery", "tests"):
            shutil.copytree(
                ROOT / folder,
                target / folder,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        path = target / "refinery" / filename
        source = path.read_text()
        assert source.count(before) == 1, name
        path.write_text(source.replace(before, after))
        result = subprocess.run(
            [sys.executable, "-m", "unittest", test, "-q"],
            cwd=target,
            env={
                **os.environ,
                "PYTHONPATH": str(target) + os.pathsep + str(target / "tests"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            timeout=45,
        )
        detected = (
            result.returncode == 1
            and "FAIL:" in result.stderr
            and "ERROR:" not in result.stderr
        )
        results.append({"mutation": name, "detected": detected})
        if not detected:
            print(result.stderr, file=sys.stderr)
print(
    json.dumps({"evidence_class": "component_mutation_detection", "results": results})
)
raise SystemExit(0 if all(row["detected"] for row in results) else 1)
