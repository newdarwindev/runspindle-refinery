#!/usr/bin/env python3
"""Isolated behavioral mutations must fail assertions, not imports or syntax."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "partial_registry_commit": (
        "storage.py",
        '        tx.execute("INSERT INTO objects VALUES(?,?,?,?)", (kind, identity, sha, data))',
        '        tx.execute("INSERT INTO objects VALUES(?,?,?,?)", (kind, identity, sha, data))\n        if kind == "approval": tx.commit()',
        "test_issue1_acceptance.IssueOneAcceptanceTests.test_registry_process_death_during_write_is_atomic",
    ),
    "feedback_rebinding": (
        "storage.py",
        "observations = lin._bounded(feedback + prior_conflicts, lin.Feedback)",
        "observations = lin._bounded(feedback, lin.Feedback)",
        "test_durable.DurableTests.test_feedback_id_cannot_rebind_across_committed_batches",
    ),
    "budget_reset": (
        "durable_evaluation.py",
        "budget = _restore(unpack(row[0]), self.clock_ms)",
        "budget = Budget(self.limits, clock_ms=self.clock_ms); budget._owners = {}",
        "test_durable.DurableTests.test_multi_connection_budget_concurrency_and_no_reset",
    ),
    "digest_only_predecessor": (
        "serving.py",
        "if not _predecessor_matches(old, predecessor) or (",
        'if (old[2] if old else None) != (predecessor["candidate_sha256"] if type(predecessor) is dict else predecessor) or (',
        "test_serving_dispatch_authority.DispatchAuthorityTests.test_replacement_cannot_overwrite_same_bytes_from_another_activation",
    ),
    "approval_bypass": (
        "serving.py",
        "if (\n"
        "            type(actor) is not str\n"
        "            or actor != expected_actor\n"
        "            or self.store is not store\n"
        "            or self.verify_approval is not verifier\n"
        "            or pack(approval) != approval_bytes\n"
        "        ):",
        "if False:",
        "test_durable.ServingTests.test_exact_approval_default_deny_forgery_and_changed_target_zero_loads",
    ),
    "serving_attribution": (
        "serving.py",
        'return {"output": value["responses"][text], "candidate_sha256": row[2]}',
        'return {"output": value["responses"][text], "candidate_sha256": "0" * 64}',
        "test_durable.ServingTests.test_load_readback_actual_output_and_failed_load_preserves_prior",
    ),
    "serving_adopts_unclaimed_intent": (
        "serving.py",
        'if state == "intent":\n            return "intent"',
        'if False:\n            return "intent"',
        "test_serving_readback_integrity.ReadbackIntegrityTests.test_unclaimed_intent_cannot_adopt_matching_loaded_bytes",
    ),
    "serving_accepts_caller_rejection": (
        "serving.py",
        "if rejected is not False:\n"
        '            raise ValueError("only the dispatching load may report a rejection")\n'
        "        return self._reconcile(api_version, activation_id, adapter, rejected=False)",
        "return self._reconcile(api_version, activation_id, adapter, rejected=rejected)",
        "test_serving_readback_integrity.ReadbackIntegrityTests.test_caller_rejection_cannot_release_claimed_reservation",
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
