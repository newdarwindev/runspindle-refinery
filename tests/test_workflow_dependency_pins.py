"""CI checkout must use the same exact Tether source as component admission."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_tether_checkout(workflow: str, repository: str, commit: str) -> None:
    # This workflow intentionally uses one explicit literal repository/ref pair.
    # A format or checkout-topology change must update this contract explicitly.
    repository_lines = re.findall(r"(?m)^\s*repository:\s*([^\s#]+)\s*$", workflow)
    refs = re.findall(
        r"(?m)^\s*repository:\s*"
        + re.escape(repository)
        + r"\s*\n\s*ref:\s*([^\s#]+)\s*$",
        workflow,
    )
    if (
        not re.fullmatch(r"[0-9a-f]{40}", commit)
        or repository_lines.count(repository) != 1
        or refs != [commit]
    ):
        raise ValueError("CI Tether checkout differs from the admitted dependency")


class WorkflowDependencyPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/validation.yml").read_text()
        self.pin = json.loads((ROOT / "dependencies/tether-feedback.json").read_text())

    def test_real_workflow_matches_admitted_source(self) -> None:
        check_tether_checkout(self.workflow, self.pin["repository"], self.pin["commit"])

    def test_stale_floating_and_missing_refs_fail(self) -> None:
        for value in (
            "c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d",
            "main",
            "",
            "${{ inputs.ref }}",
        ):
            changed = self.workflow.replace(self.pin["commit"], value)
            with self.subTest(ref=value), self.assertRaises(ValueError):
                check_tether_checkout(
                    changed, self.pin["repository"], self.pin["commit"]
                )

    def test_duplicate_and_wrong_repository_fail(self) -> None:
        pair = (
            "\nrepository: "
            + self.pin["repository"]
            + "\nref: "
            + self.pin["commit"]
            + "\n"
        )
        for value in (
            self.workflow + pair,
            self.workflow.replace(self.pin["repository"], "wrong/repository"),
        ):
            with self.assertRaises(ValueError):
                check_tether_checkout(value, self.pin["repository"], self.pin["commit"])
