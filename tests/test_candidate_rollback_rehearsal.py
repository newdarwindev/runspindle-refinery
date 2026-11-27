"""Rehearse invented candidate/rollback receipts, not real serving operations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import unittest


SCRIPT = Path(__file__).resolve()
FIXTURE = SCRIPT.with_name("fixtures") / "refinery-rollback-rehearsal.json"
CASES = {
    "verified": ("verified_fixture", ["candidate", "rollback"]),
    "missing-predecessor": ("missing_predecessor", []),
    "wrong-approval": ("approval_mismatch", []),
    "wrong-lineage": ("approval_mismatch", []),
    "wrong-holdout": ("approval_mismatch", []),
    "wrong-approved-predecessor": ("approval_mismatch", []),
    "unknown-candidate": ("candidate_readback_unknown", ["candidate"]),
    "wrong-candidate": ("candidate_readback_mismatch", ["candidate"]),
    "unknown-rollback": ("rollback_readback_unknown", ["candidate", "rollback"]),
    "wrong-rollback": ("rollback_readback_mismatch", ["candidate", "rollback"]),
}


def evaluate(fixture: dict[str, Any], case: str) -> dict[str, Any]:
    candidate = dict(fixture["candidate"])
    approval = dict(fixture["approval"])
    current = fixture["predecessor"]
    witness = dict(fixture["witness"])
    if case == "missing-predecessor":
        current = None
    elif case == "wrong-approval":
        approval["id"] = "fixture-unrelated"
    elif case == "wrong-lineage":
        approval["lineage"] = "fixture-other-lineage"
    elif case == "wrong-holdout":
        approval["holdout"] = "fixture-other-holdout"
    elif case == "wrong-approved-predecessor":
        approval["predecessor"] = "fixture-unrelated"
    elif case == "unknown-candidate":
        witness["candidate"] = None
    elif case == "wrong-candidate":
        witness["candidate"] = "fixture-unrelated"
    elif case == "unknown-rollback":
        witness["restored"] = None
    elif case == "wrong-rollback":
        witness["restored"] = "fixture-unrelated"

    table: dict[str, Any] = {"current": current}
    effects: list[str] = []
    flags = {"candidate": False, "rollback": False}

    def compare_independent(stage: str, expected: str) -> str | None:
        observed = witness.get(stage)
        label = "rollback" if stage == "restored" else stage
        if observed is None:
            return f"{label}_readback_unknown"
        if observed != expected or table["current"] != expected:
            return f"{label}_readback_mismatch"
        flags[label] = True
        return None

    try:
        if not isinstance(current, str) or not current:
            outcome = "missing_predecessor"
        elif current != fixture["reviewed_predecessor"]:
            outcome = "predecessor_mismatch"
        elif set(candidate) != {"id", "lineage", "holdout"} or {**candidate, "predecessor": current} != approval:
            outcome = "approval_mismatch"
        else:
            table["current"] = candidate["id"]
            effects.append("candidate")
            outcome = compare_independent("candidate", candidate["id"])
            if outcome is None:
                table["current"] = current
                effects.append("rollback")
                outcome = compare_independent("restored", current)
            if outcome is None:
                outcome = "verified_fixture"
    finally:
        table.clear()

    expected_outcome, expected_effects = CASES[case]
    verified = all(flags.values())
    return {
        "synthetic_only": True,
        "live_serving_verified": False,
        "scenario": case,
        "outcome": outcome,
        "effects": effects,
        "readback": flags,
        "rollback_fixture_verified": verified,
        "teardown_cleared": not table,
        "passed": (
            not table
            and outcome == expected_outcome
            and effects == expected_effects
            and verified == (case == "verified")
        ),
    }


class CandidateRollbackRehearsalTests(unittest.TestCase):
    def test_cli_cases_are_repeatable_and_fail_closed(self) -> None:
        for case, (outcome, expected_effects) in CASES.items():
            with self.subTest(case=case):
                cmd = [sys.executable, str(SCRIPT), case]
                receipt = subprocess.check_output(cmd, text=True)
                self.assertEqual(receipt, subprocess.check_output(cmd, text=True))
                result = json.loads(receipt)
                self.assertTrue(result["passed"])
                self.assertTrue(result["synthetic_only"])
                self.assertFalse(result["live_serving_verified"])
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(result["effects"], expected_effects)
                self.assertEqual(result["rollback_fixture_verified"], case == "verified")
                self.assertTrue(result["teardown_cleared"])

    def test_current_predecessor_must_equal_reviewed_predecessor(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        result = evaluate({**fixture, "predecessor": "fixture-unreviewed"}, "verified")
        self.assertEqual(result["outcome"], "predecessor_mismatch")
        self.assertEqual(result["effects"], [])
        self.assertFalse(result["passed"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=CASES)
    args = parser.parse_args()
    result = evaluate(json.loads(FIXTURE.read_text(encoding="utf-8")), args.scenario)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
