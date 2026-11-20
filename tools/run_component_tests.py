"""Run Refinery component tests in isolated CI checkouts."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from hygiene import source_fingerprint


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def discover():
    loader = unittest.TestLoader()
    discovered = list(cases(loader.discover("tests")))
    identities = [test.id() for test in discovered]
    if loader.errors or not identities or len(set(identities)) != len(identities):
        raise RuntimeError("component test discovery is incomplete or ambiguous")
    return discovered, identities


def assign(identities):
    modules = defaultdict(list)
    for identity in identities:
        modules[identity.split(".", 1)[0]].append(identity)
    shards = [[], []]
    for module in sorted(modules, key=lambda name: (-len(modules[name]), name)):
        min(shards, key=len).extend(modules[module])
    if any(not shard for shard in shards):
        raise RuntimeError("expected two nonempty component test shards")
    return shards


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def clone(source, destination, revision):
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


def command(*args):
    return [sys.executable, str(Path(__file__).resolve()), *args]


def run_parallel():
    root = Path.cwd()
    tether = Path(os.environ["RUNSPINDLE_TETHER_SOURCE"]).resolve(strict=True)
    revision = git("rev-parse", "HEAD")
    pin = json.loads((root / "dependencies/tether-feedback.json").read_text())
    if git("-C", str(tether), "rev-parse", "HEAD") != pin["commit"]:
        raise RuntimeError("prepared Tether checkout differs from the component lock")
    with tempfile.TemporaryDirectory(prefix="refinery-component-") as scratch:
        workspace = Path(scratch)
        checkouts, environments = [], []
        for index in range(2):
            checkout = workspace / f"shard-{index}"
            clone(root, checkout, revision)
            pinned = checkout / ".feedback-deps" / "tether"
            pinned.parent.mkdir()
            clone(tether, pinned, pin["commit"])
            temporary = checkout / ".git" / "component-tmp"
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
        workers = []
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


def main():
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
