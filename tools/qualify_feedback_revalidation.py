#!/usr/bin/env python3
"""Bounded actual-old/current-source receipt transition, synthetic authorities only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REFINERY = "f8a3f4199994f65ee42bbf2d9cfcae0494dae03d"
PREVIOUS_TETHER = "c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d"
CURRENT_TETHER = "ae5ac216b45ca413283e4200c0a4a61054998b58"


def exact_checkout(path: Path, commit: str) -> None:
    head = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
        text=True,
    )
    if head != commit or dirty:
        raise ValueError("qualification input is not the exact clean checkout")


def child(
    repo: Path, tether: Path, state: Path, phase: str, prior: str = ""
) -> dict[str, Any]:
    script = r"""
import json, sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
from test_source_feedback import SourceFixture
from refinery.storage import pack
from refinery.lineage import digest
# Existing fixture imports are permitted; all actual network attempts are denied.
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(AssertionError('network forbidden')) if event in ('socket.__new__','socket.getaddrinfo') else None)
root, phase, prior = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
h = SourceFixture(root, prepare=(phase == 'old'))
try:
    if phase == 'old':
        h.ingest()
        snapshot = h.collect()
        h.verify_use(snapshot)
        print(json.dumps({'snapshot': snapshot, 'dependency': h.dep.pin['commit']}))
    else:
        old_bytes = pack(h.store.get('lineage', prior))
        calls = list(h.provider.requests)
        try: h.verify_use(prior)
        except ValueError: pass
        else: raise AssertionError('old receipt acquired new use authority')
        current = h.collect(prior_snapshot_sha256=prior, collection_ref='fresh-collection', training_ref='fresh-training')
        h.verify_use(current)
        if pack(h.store.get('lineage', prior)) != old_bytes: raise AssertionError('historical snapshot rewritten')
        if h.store.lineage(prior) != h.store.lineage(current): raise AssertionError('dataset or partition changed')
        if h.provider.requests != calls: raise AssertionError('provider effects')
        links = h.store.db.execute("SELECT count(*) FROM objects WHERE kind='feedback_revalidation'").fetchone()[0]
        if links != 1: raise AssertionError('missing or duplicated durable link')
        print(json.dumps({'snapshot': current, 'prior_snapshot': prior, 'dependency': h.dep.pin['commit'], 'links': links, 'provider_calls': 0, 'prior_bytes_sha256': digest(old_bytes)}))
finally:
    h.close()
"""
    env = dict(os.environ, RUNSPINDLE_TETHER_SOURCE=str(tether))
    result = subprocess.run(
        [sys.executable, "-c", script, str(state), phase, prior],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return dict(json.loads(result.stdout))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-refinery", type=Path, required=True)
    parser.add_argument("--previous-tether", type=Path, required=True)
    parser.add_argument("--current-tether", type=Path, required=True)
    args = parser.parse_args()
    for path, commit in (
        (args.previous_refinery, PREVIOUS_REFINERY),
        (args.previous_tether, PREVIOUS_TETHER),
        (args.current_tether, CURRENT_TETHER),
    ):
        exact_checkout(path, commit)
    with tempfile.TemporaryDirectory(prefix="refinery-revalidation-") as temporary:
        state = Path(temporary)
        old = child(
            args.previous_refinery.resolve(),
            args.previous_tether.resolve(),
            state,
            "old",
        )
        first = child(
            ROOT, args.current_tether.resolve(), state, "new", old["snapshot"]
        )
        repeat = child(
            ROOT, args.current_tether.resolve(), state, "new", old["snapshot"]
        )
        if first != repeat:
            raise ValueError("fresh-process revalidation is not idempotent")
    print(
        json.dumps(
            {
                "status": "passed",
                "classification": "isolated_real_stores_synthetic_authorities",
                "previous": old,
                "current": first,
                "fresh_process_replay": "passed",
                "temporary_state_cleanup": not state.exists(),
                "implementation_sha256": hashlib.sha256(
                    (ROOT / "refinery/feedback_collection.py").read_bytes()
                ).hexdigest(),
                "limits": "no live/native authority, no production, no storage authentication",
            },
            sort_keys=True,
        )
    )


def stop(signum: int, frame: Any) -> None:
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    main()
