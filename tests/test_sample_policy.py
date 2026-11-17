"""Minimum heldout denominator, distinct policy identity, no serving authority."""

import importlib
import inspect
import json
import socket
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from refinery import serving
from refinery.lineage import digest
from refinery.storage import VERSION, Store, pack, unpack

fixtures = importlib.import_module("test_qualification_thresholds")
ROOT = Path(__file__).resolve().parents[1]


class SamplePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "refinery.sqlite"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.registry = serving.Registry(self.store)
        network = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(spy.assert_not_called)
        loader = patch.object(
            serving.LocalServing,
            "load",
            side_effect=AssertionError("activation forbidden"),
        )
        load_spy = loader.start()
        self.addCleanup(loader.stop)
        self.addCleanup(load_spy.assert_not_called)

    def policy(self, count: int) -> serving.SamplePolicy:
        return serving.SamplePolicy(
            "local", 0, 0, 20000, 20000, min_heldout_cases=count
        )

    def rows(self) -> tuple[str, ...]:
        return tuple(self.store.db.iterdump())

    def test_exact_boundary_and_distinct_policy_identity_bind_approval(self) -> None:
        _, provider = fixtures.evaluated(self.store, 3, 1, 3)
        before, calls = self.rows(), len(provider.calls)
        with self.assertRaisesRegex(ValueError, "insufficient heldout"):
            self.registry.qualify(VERSION, "evaluation", self.policy(4))
        self.assertEqual(self.rows(), before)
        strict = self.registry.qualify(VERSION, "evaluation", self.policy(3))
        loose = self.registry.qualify(VERSION, "evaluation", self.policy(1))
        self.assertNotEqual(strict, loose)
        candidate = self.store.get("candidate", strict)
        other = self.store.get("candidate", loose)
        self.assertNotEqual(candidate["policy_sha256"], other["policy_sha256"])
        grant = serving.Approval(
            candidate["candidate_sha256"],
            candidate["evaluation_sha256"],
            other["policy_sha256"],
            "local",
            "actor",
            "synthetic-approval",
        )
        with self.assertRaisesRegex(ValueError, "approval binding conflict"):
            self.registry.begin(
                VERSION, "forged-policy", strict, grant, expected_generation=0
            )
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM activations").fetchone(), (0,)
        )
        self.assertEqual(len(provider.calls), calls)

    def test_retries_preparation_and_baseline_calls_do_not_count_as_cases(self) -> None:
        report, provider = fixtures.evaluated(self.store, 3, 1, 3, prepared_retry=True)
        self.assertEqual(len(report.heldout_cases), 3)
        self.assertGreater(len(provider.calls), 4)
        before = self.rows()
        with self.assertRaisesRegex(ValueError, "insufficient heldout"):
            self.registry.qualify(VERSION, "evaluation", self.policy(4))
        self.assertEqual(self.rows(), before)

    def test_invalid_counts_and_foreign_policy_fail_before_store_changes(self) -> None:
        before = self.rows()
        for count in (True, False, 0, -1, 1.0, "3", 1001, 2**64):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.policy(count)  # type: ignore[arg-type]
        altered = self.policy(3)
        object.__setattr__(altered, "min_heldout_cases", 0)
        with self.assertRaises(ValueError):
            self.registry.qualify(VERSION, "missing", altered)

        class Foreign:
            def __post_init__(self) -> None:
                raise AssertionError("foreign policy callback invoked")

        with self.assertRaisesRegex(ValueError, "unsupported serving policy type"):
            self.registry.qualify(VERSION, "missing", cast(Any, Foreign()))
        self.assertEqual(self.rows(), before)

    def test_instance_validation_hook_cannot_hide_invalid_sample_bound(self) -> None:
        for count in (0, 1001, True):
            with self.subTest(count=count):
                policy = self.policy(3)
                object.__setattr__(policy, "min_heldout_cases", count)

                def forbidden() -> None:
                    raise AssertionError("instance validation hook invoked")

                object.__setattr__(policy, "__post_init__", forbidden)
                with patch.object(
                    self.store,
                    "get",
                    side_effect=AssertionError("store read forbidden"),
                ) as read:
                    with self.assertRaises(ValueError):
                        self.registry.qualify(VERSION, "missing", policy)
                    read.assert_not_called()

    def test_legacy_policy_codec_bytes_unchanged_and_new_type_roundtrips(self) -> None:
        legacy = serving.Policy("local", 0, 0, 20000, 20000)
        expected = b'{"fields":{"environment":"local","format":"synthetic.map.v1","max_cost_microunits":20000,"max_latency_ms":20000,"min_improvement_ppm":0,"min_quality_ppm":0},"type":"Policy"}'
        self.assertEqual(pack(legacy), expected)
        self.assertEqual(pack(unpack(expected)), expected)
        sample = self.policy(3)
        self.assertNotEqual(digest(pack(sample)), digest(expected))
        self.assertEqual(type(unpack(pack(sample))), serving.SamplePolicy)
        self.assertEqual(unpack(pack(sample)), sample)
        with self.store.transaction() as tx:
            cast(Any, self.store).put(tx, "policy", digest(expected), legacy)
            cast(Any, self.store).put(tx, "policy", digest(pack(sample)), sample)
        self.assertEqual(pack(self.store.get("policy", digest(expected))), expected)
        self.assertEqual(
            self.store.get("policy", digest(pack(sample))).min_heldout_cases, 3
        )

    def test_fresh_process_checks_persisted_evaluation_without_provider_or_loader(
        self,
    ) -> None:
        fixtures.evaluated(self.store, 3, 1, 3)
        code = """
import json,socket,sys
from unittest.mock import patch
from refinery.serving import Registry,SamplePolicy,LocalServing
from refinery.storage import Store,VERSION
from refinery.evaluation import ScriptedProvider
with Store(sys.argv[1]) as store:
 r=Registry(store); before=tuple(store.db.iterdump()); denied=False
 with patch.object(socket,'socket',side_effect=AssertionError('network forbidden')) as network, patch.object(ScriptedProvider,'complete',side_effect=AssertionError('provider forbidden')) as provider, patch.object(LocalServing,'load',side_effect=AssertionError('load forbidden')) as load:
  try:r.qualify(VERSION,'evaluation',SamplePolicy('local',0,0,20000,20000,min_heldout_cases=4))
  except ValueError:denied=True
  unchanged=before==tuple(store.db.iterdump())
  key=r.qualify(VERSION,'evaluation',SamplePolicy('local',0,0,20000,20000,min_heldout_cases=3))
  candidate=store.get('candidate',key)
  policy=store.get('policy',candidate['policy_sha256'])
  network.assert_not_called();provider.assert_not_called();load.assert_not_called()
  print(json.dumps({'denied':denied,'unchanged':unchanged,'count':policy.min_heldout_cases,'activations':store.db.execute('SELECT count(*) FROM activations').fetchone()[0]}))
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", code, str(self.path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"denied": True, "unchanged": True, "count": 3, "activations": 0},
        )

    def test_removed_sample_guard_exposes_forbidden_candidate(self) -> None:
        fixtures.evaluated(self.store, 3, 1, 3)
        source = textwrap.dedent(inspect.getsource(serving.Registry.qualify))
        guard = "if isinstance(policy, SamplePolicy) and denominator < policy.min_heldout_cases:"
        self.assertEqual(source.count(guard), 1)
        scope: dict[str, Any] = dict(vars(serving))
        exec(
            compile(
                source.replace(guard, "if False:  # deliberate defect"),
                "<sample-policy-mutation>",
                "exec",
            ),
            scope,
        )
        with self.assertRaisesRegex(ValueError, "insufficient heldout"):
            self.registry.qualify(VERSION, "evaluation", self.policy(4))
        with patch.object(serving.Registry, "qualify", scope["qualify"]):
            with self.assertRaises(AssertionError):
                with self.assertRaises(ValueError):
                    self.registry.qualify(VERSION, "evaluation", self.policy(4))
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM objects WHERE kind='candidate'"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM activations").fetchone(), (0,)
        )


if __name__ == "__main__":
    unittest.main()
