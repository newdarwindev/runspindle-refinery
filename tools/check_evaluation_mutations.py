#!/usr/bin/env python3
"""Prove independent evaluation assertions reject deliberate implementation bugs."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / "refinery/evaluation.py").read_text()
mutations = {
    "ignore_call_ceiling": ("if self.calls >= self.limits.calls:", "if False:"),
    "unknown_safety_passes": ('if reply.safety is not True:', 'if reply.safety is False:'),
    "ignore_locked_split": ('    split.verify(dataset)', '    pass  # deliberately bypass lock'),
}
results = []
for name, (before, after) in mutations.items():
    assert source.count(before) == 1, name
    with tempfile.TemporaryDirectory(prefix="refinery-mutation-") as temporary:
        directory = Path(temporary)
        shutil.copytree(root / "refinery", directory / "refinery", ignore=shutil.ignore_patterns("__pycache__"))
        (directory / "refinery/evaluation.py").write_text(source.replace(before, after))
        (directory / "tests").mkdir()
        for filename in ("test_evaluation.py", "test_lineage.py"):
            shutil.copy2(root / "tests" / filename, directory / "tests" / filename)
        completed = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_evaluation.py", "-q"],
                                   cwd=directory, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                   capture_output=True, text=True, timeout=30)
        # Nonzero must be assertion failure, not an import/syntax/tool failure.
        detected = completed.returncode == 1 and "FAIL:" in completed.stderr and "ERROR:" not in completed.stderr
        results.append({"mutation": name, "detected": detected})
        if not detected:
            print(completed.stderr, file=sys.stderr)
print(json.dumps({"evidence_class": "component_mutation_detection", "results": results}, sort_keys=True))
raise SystemExit(0 if all(r["detected"] for r in results) else 1)
