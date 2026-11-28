"""Review an invented PR stack locally; never query or mutate GitHub."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import unittest


SCRIPT = Path(__file__).resolve()
FIXTURE = SCRIPT.with_name("fixtures") / "synthetic-export-graph.json"
ORDER = tuple(range(99, 104))
CASES = {
    "next-review": ("fixture_reviewable", 102, None),
    "stale-root-head": ("wrong_head", None, 99),
    "missing-root-review": ("missing_exact_review", None, 99),
    "stale-checks": ("missing_exact_checks", None, 100),
    "wrong-base": ("wrong_base", None, 101),
}


def evaluate(data: dict[str, Any]) -> tuple[str, int | None, int | None]:
    rows = data["nodes"]
    nodes = {n["number"]: n for n in rows}
    if len(nodes) != len(rows) or set(nodes) != set(ORDER):
        return "bad_fixture_graph", None, None
    expected_heads = data["expected_heads"]
    predecessor: int | None = None
    expected_base = data["tree_entry"]

    for number in ORDER:
        node = nodes[number]
        head = expected_heads[str(number)]
        if node.get("head") != head:
            return "wrong_head", None, number
        if node.get("predecessor") != predecessor or node.get("base") != expected_base:
            return "wrong_base", None, number
        if node.get("checks") != {"head": head, "validation": "passed"}:
            return "missing_exact_checks", None, number
        review = node.get("review")
        if not isinstance(review, dict) or review.get("head") != head:
            return "missing_exact_review", None, number
        if node.get("merged") is True:
            if review.get("state") != "approved":
                return "missing_exact_review", None, number
        else:
            if review.get("state") not in {"requested", "approved"}:
                return "missing_exact_review", None, number
            return "fixture_reviewable", number, None
        predecessor, expected_base = number, head
    return "fixture_stack_completed", None, None


def rehearse(scenario: str) -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = {n["number"]: n for n in data["nodes"]}
    if scenario == "stale-root-head":
        rows[99]["head"] = "fixture-new-unreviewed-head"
    elif scenario == "missing-root-review":
        del rows[99]["review"]
    elif scenario == "stale-checks":
        rows[100]["checks"]["head"] = "fixture-earlier-head"
    elif scenario == "wrong-base":
        rows[101]["base"] = "fixture-different-base"

    actual = evaluate(data)
    return {
        "synthetic_only": True,
        "live_pr_stack_verified": False,
        "scenario": scenario,
        "outcome": actual[0],
        "next_reviewable_fixture": actual[1],
        "first_blocked_fixture": actual[2],
        "passed": actual == CASES[scenario],
    }


class SyntheticExportGraphTests(unittest.TestCase):
    def test_repeatable_cli_receipts(self) -> None:
        for scenario, expected in CASES.items():
            with self.subTest(scenario=scenario):
                cmd = [sys.executable, str(SCRIPT), scenario]
                output = subprocess.check_output(cmd, text=True)
                self.assertEqual(output, subprocess.check_output(cmd, text=True))
                result = json.loads(output)
                self.assertTrue(result["synthetic_only"])
                self.assertFalse(result["live_pr_stack_verified"])
                self.assertTrue(result["passed"])
                self.assertEqual(
                    (result["outcome"], result["next_reviewable_fixture"], result["first_blocked_fixture"]),
                    expected,
                )

    def test_prior_request_is_not_approval_and_future_approval_does_not_leapfrog(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["nodes"][0]["review"]["state"] = "requested"
        self.assertEqual(evaluate(data), ("missing_exact_review", None, 99))
        data["nodes"][0]["review"]["state"] = "approved"
        data["nodes"][4]["review"]["state"] = "approved"
        self.assertEqual(evaluate(data), ("fixture_reviewable", 102, None))

    def test_reordering_input_does_not_reorder_dependent_review(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["nodes"].reverse()
        self.assertEqual(evaluate(data), ("fixture_reviewable", 102, None))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=CASES)
    receipt = rehearse(parser.parse_args().scenario)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
