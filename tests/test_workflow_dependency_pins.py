"""CI checkout must use the same exact Tether source as component admission."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_REF = "${{ steps.tether-pin.outputs.commit }}"


def check_tether_checkout(workflow: str, repository: str) -> str:
    repository_lines = re.findall(r"(?m)^\s*repository:\s*([^\s#]+)\s*$", workflow)
    refs = re.findall(
        r"(?m)^\s*repository:\s*"
        + re.escape(repository)
        + r"\s*\n\s*ref:[ \t]*([^\n#]+?)[ \t]*$",
        workflow,
    )
    if repository_lines.count(repository) != 1 or refs != [CHECKOUT_REF]:
        raise ValueError("CI Tether checkout differs from the admitted dependency")
    scripts = re.findall(
        r"(?m)^        id: tether-pin\n        run: \|\n"
        r"          python3 - <<'PYTHON'\n(.*?)^          PYTHON$",
        workflow,
        re.DOTALL,
    )
    if len(scripts) != 1:
        raise ValueError("CI must read the admitted dependency using one lock step")
    return textwrap.dedent(scripts[0])


class WorkflowDependencyPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/validation.yml").read_text()
        self.pin = json.loads((ROOT / "dependencies/tether-feedback.json").read_text())

    def test_real_workflow_matches_admitted_source(self) -> None:
        self.check_lock(self.pin, f"commit={self.pin['commit']}\n")

    def check_lock(self, pin: dict[str, object], expected: str | None) -> None:
        script = check_tether_checkout(self.workflow, self.pin["repository"])
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "dependencies").mkdir()
            (root / "dependencies/tether-feedback.json").write_text(json.dumps(pin))
            output = root / "output"
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env={**os.environ, "GITHUB_OUTPUT": str(output)},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if expected is None:
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), expected)

    def test_floating_and_missing_lock_commits_fail(self) -> None:
        for value in ("main", "", None, self.pin["commit"].upper()):
            with self.subTest(commit=value):
                self.check_lock({**self.pin, "commit": value}, None)
        self.check_lock({**self.pin, "repository": "wrong/repository"}, None)

    def test_stale_floating_and_missing_checkout_refs_fail(self) -> None:
        for value in (
            "c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d",
            "main",
            "",
            "${{ inputs.ref }}",
        ):
            changed = self.workflow.replace(CHECKOUT_REF, value)
            with self.subTest(ref=value), self.assertRaises(ValueError):
                check_tether_checkout(changed, self.pin["repository"])

    def test_duplicate_and_wrong_repository_fail(self) -> None:
        pair = (
            "\nrepository: " + self.pin["repository"] + "\nref: " + CHECKOUT_REF + "\n"
        )
        for value in (
            self.workflow + pair,
            self.workflow.replace(self.pin["repository"], "wrong/repository"),
        ):
            with self.assertRaises(ValueError):
                check_tether_checkout(value, self.pin["repository"])
