"""Run Refinery component tests in isolated CI checkouts."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from tools.hygiene import source_fingerprint

# Rounded measurements from the two-worker component profile on 2026-11-20.
# Keep modules intact so their fixtures still run once in a single worker.
MODULE_SECONDS = {
    "test_dataset_batch": 21,
    "test_dataset_materialization": 21,
    "test_durable": 27,
    "test_durable_dspy": 41,
    "test_durable_gepa": 31,
    "test_durable_optimization": 22,
    "test_evaluation_admission": 16,
    "test_feedback_collection": 11,
    "test_feedback_projection": 19,
    "test_feedback_use": 51,
    "test_gepa_heldout": 40,
    "test_gepa_heldout_materialized": 30,
    "test_materialized_durable": 62,
    "test_qualification_thresholds": 98,
    "test_serving_dispatch_authority": 17,
    "test_serving_observation": 11,
}


def cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        elif isinstance(item, unittest.TestCase):
            yield item
        else:
            raise TypeError("unexpected unittest discovery item")


def discover() -> tuple[list[unittest.TestCase], list[str]]:
    loader = unittest.TestLoader()
    discovered = list(cases(loader.discover("tests")))
    identities = [test.id() for test in discovered]
    if loader.errors or not identities or len(set(identities)) != len(identities):
        raise RuntimeError("component test discovery is incomplete or ambiguous")
    return discovered, identities


def assign(identities: list[str]) -> list[list[str]]:
    modules: defaultdict[str, list[str]] = defaultdict(list)
    for identity in identities:
        modules[identity.split(".", 1)[0]].append(identity)
    weights = {
        name: float(MODULE_SECONDS[name])
        if name in MODULE_SECONDS
        else len(tests) * 0.2
        for name, tests in modules.items()
    }
    shards: list[list[str]] = [[], []]
    totals = [0.0, 0.0]
    for module in sorted(modules, key=lambda name: (-weights[name], name)):
        index = 0 if totals[0] <= totals[1] else 1
        shards[index].extend(modules[module])
        totals[index] += weights[module]
    if any(not shard for shard in shards):
        raise RuntimeError("expected two nonempty component test shards")
    return shards


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def clone(source: Path, destination: Path, revision: str) -> None:
    git(
        "clone",
        "--quiet",
        "--no-hardlinks",
        "--no-checkout",
        "--",
        str(source),
        str(destination),
    )
    git("-C", str(destination), "checkout", "--quiet", "--detach", revision)


def command(*args: str) -> list[str]:
    return [sys.executable, "-m", "tools.run_component_tests", *args]


def run_parallel() -> int:
    root = Path.cwd()
    tether = Path(os.environ["RUNSPINDLE_TETHER_SOURCE"]).resolve(strict=True)
    revision = git("rev-parse", "HEAD")
    pin = json.loads((root / "dependencies/tether-feedback.json").read_text())
    if git("-C", str(tether), "rev-parse", "HEAD") != pin["commit"]:
        raise RuntimeError("prepared Tether checkout differs from the component lock")
    with tempfile.TemporaryDirectory(prefix="rf-") as scratch:
        workspace = Path(scratch)
        checkouts, environments = [], []
        for index in range(2):
            checkout = workspace / f"shard-{index}"
            clone(root, checkout, revision)
            pinned = checkout / ".feedback-deps" / "tether"
            pinned.parent.mkdir()
            clone(tether, pinned, pin["commit"])
            # Keep Unix socket fixture paths short and outside the checkout.
            temporary = workspace / f"t{index}"
            temporary.mkdir()
            checkouts.append(checkout)
            environments.append(
                dict(
                    os.environ,
                    GITHUB_WORKSPACE=str(checkout),
                    RUNSPINDLE_TETHER_SOURCE=str(pinned),
                    TMPDIR=str(temporary),
                    PYTHONDONTWRITEBYTECODE="1",
                )
            )
        inventory = workspace / "inventory.json"
        subprocess.run(
            command("--discover", str(inventory)),
            cwd=checkouts[0],
            env=environments[0],
            check=True,
        )
        identities = json.loads(inventory.read_text())
        workers: list[subprocess.Popen[bytes]] = []
        try:
            for index, shard in enumerate(assign(identities)):
                plan = workspace / f"shard-{index}.json"
                plan.write_text(json.dumps(shard))
                workers.append(
                    subprocess.Popen(
                        command("--inventory", str(inventory), "--shard", str(plan)),
                        cwd=checkouts[index],
                        env=environments[index],
                    )
                )
            statuses = [worker.wait() for worker in workers]
            return 0 if statuses == [0, 0] else 1
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--discover", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--shard", type=Path)
    args = parser.parse_args()
    if args.discover or (args.inventory and args.shard):
        sys.path.insert(0, str(Path.cwd()))
        tests, identities = discover()
        if args.discover:
            args.discover.write_text(json.dumps(identities))
            return 0
        if identities != json.loads(args.inventory.read_text()):
            raise RuntimeError("worker discovery differs from component test inventory")
        assigned = set(json.loads(args.shard.read_text()))
        selected = [test for test in tests if test.id() in assigned]
        if {test.id() for test in selected} != assigned:
            raise RuntimeError("worker could not resolve every assigned component test")
        root = Path.cwd()
        before = source_fingerprint(root)
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
        module_by_test = {str(test): test.id().split(".", 1)[0] for test in selected}
        module_seconds: defaultdict[str, float] = defaultdict(float)
        durations: list[tuple[str, float]] = getattr(result, "collectedDurations", [])
        for description, seconds in durations:
            module = module_by_test.get(description)
            if module is not None:
                module_seconds[module] += seconds
        print(
            "REFINERY_COMPONENT_MODULE_SECONDS "
            + json.dumps(
                {
                    module: round(seconds, 3)
                    for module, seconds in module_seconds.items()
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if source_fingerprint(root) != before:
            raise RuntimeError("component test shard changed its source tree")
        return 0 if result.wasSuccessful() and result.testsRun == len(selected) else 1
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return run_parallel()
    os.execv(
        sys.executable,
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
