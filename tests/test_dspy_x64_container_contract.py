"""Synthetic byte/configuration checks only; no Docker or DSPy execution."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import dspy_x64_container_contract as policy
from tools import dspy_x64_container_smoke as smoke


class ContainerPolicyTests(unittest.TestCase):
    def observation(self, spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "Image": spec["Image"],
            "Mounts": [],
            "Config": {
                key: copy.deepcopy(value)
                for key, value in spec.items()
                if key not in {"HostConfig", "Image"}
            },
            "HostConfig": copy.deepcopy(spec["HostConfig"]),
        }

    def test_exact_inert_spec_and_confinement_negatives(self) -> None:
        spec = policy.create_spec("sha256:" + "a" * 64, "b" * 64, "c" * 40)
        policy.check_observation(spec, self.observation(spec))
        for field, value in (
            ("Privileged", True),
            ("ReadonlyRootfs", False),
            ("NetworkMode", "host"),
            ("CapAdd", ["SYS_ADMIN"]),
            ("PidMode", "host"),
            ("PidsLimit", 0),
            ("Memory", 0),
            ("AutoRemove", True),
            ("RestartPolicy", {"Name": "always"}),
            ("LogConfig", {"Type": "json-file", "Config": {"max-size": "1m"}}),
        ):
            observed = self.observation(spec)
            observed["HostConfig"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                policy.check_observation(spec, observed)
        for changed_field, changed_value in (
            ("Image", "sha256:" + "c" * 64),
            ("Mounts", [{"Source": "/host"}]),
        ):
            observed = self.observation(spec)
            observed[changed_field] = changed_value
            with self.subTest(field=changed_field), self.assertRaises(ValueError):
                policy.check_observation(spec, observed)
        observed = self.observation(spec)
        observed["Config"]["Env"].append("PRIVATE_TOKEN=synthetic")
        with self.assertRaises(ValueError):
            policy.check_observation(spec, observed)

    def test_mutable_images_and_malformed_digests_refused(self) -> None:
        for image, digest in (
            ("ubuntu:latest", "b" * 64),
            ("sha256:bad", "b" * 64),
            ("sha256:" + "a" * 64, "bad"),
        ):
            with self.subTest(image=image), self.assertRaises(ValueError):
                policy.create_spec(image, digest, "c" * 40)

    def test_output_deadline_and_descendant_evidence_are_required(self) -> None:
        spec = policy.create_spec("sha256:" + "a" * 64, "b" * 64, "c" * 40)
        observed = self.observation(spec)
        observed["State"] = {
            "Running": False,
            "Pid": 0,
            "ExitCode": 0,
            "OOMKilled": False,
            "Error": "",
        }
        result = {
            "status": "passed",
            "evidence_class": "actual_framework_synthetic_completion_smoke",
            "candidate_sha256": smoke.CANDIDATE_SHA256,
            "source_sha": "c" * 40,
            "actual_framework": "DSPy 3.1.0",
            "quality": "synthetic_only",
            "provider_calls": 1,
            "lm_calls": 1,
            "bootstrap": "not_run",
            "restart": "not_run",
            "live_provider": "not_run",
            "serving": "not_run",
        }
        kwargs: dict[str, Any] = {
            "elapsed_seconds": 1.0,
            "deadline_or_output_stop": False,
            "streams_fully_drained": True,
            "cgroup_populated": False,
        }
        stdout = policy.encoded(result)
        self.assertEqual(
            policy.check_result(spec, observed, stdout, b"", **kwargs), result
        )
        for key, value in (
            ("elapsed_seconds", 121.0),
            ("deadline_or_output_stop", True),
            ("streams_fully_drained", False),
            ("cgroup_populated", True),
            ("cgroup_populated", None),
        ):
            changed = {**kwargs, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                policy.check_result(spec, observed, stdout, b"", **changed)
        with self.assertRaises(ValueError):
            policy.check_result(spec, observed, stdout, b"x" * 1048577, **kwargs)
        observed["State"]["ExitCode"] = 1
        with self.assertRaises(ValueError):
            policy.check_result(spec, observed, stdout, b"", **kwargs)


class ImageContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("source", "runtime", "deps", "evidence", "lib/x86_64-linux-gnu"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "source/example.py").write_bytes(b"synthetic source")
        (self.root / "runtime/python").write_bytes(b"synthetic interpreter bytes")
        (self.root / "deps/example.py").write_bytes(b"synthetic dependency")
        native = self.root / "lib/x86_64-linux-gnu/libc.so.6"
        native.write_bytes(b"synthetic native bytes; never executed")
        candidate = {
            "status": "UNREVIEWED_CANDIDATE_NOT_EXECUTION_AUTHORITY",
            "candidate_manifest": {
                "dependency_lock_sha256": "830486665f562043c93297ff9da93db9b379c13cf12eba680b320dc1c5b4b525",
                "dependencies": [{} for _ in range(76)],
                "dspy": "3.1.0",
                "installed_files": {
                    "example.py": smoke.hash_file(self.root / "deps/example.py")
                },
            },
        }
        data = json.dumps(candidate).encode()
        (self.root / "evidence/candidate.json").write_bytes(data)
        self.digest = hashlib.sha256(data).hexdigest()
        self.enterContext(patch.object(smoke, "CANDIDATE_SHA256", self.digest))
        self.contract = {
            "schema": policy.SCHEMA,
            "source_sha": "a" * 40,
            "candidate_sha256": self.digest,
            "trees": {
                name: smoke.tree(self.root / name)
                for name in ("runtime", "deps", "source")
            },
            "native": {"lib/x86_64-linux-gnu/libc.so.6": smoke.hash_file(native)},
            "native_scope": "static_DT_NEEDED_closure_dynamic_import_unqualified",
            "fixture_sha256": policy.digest(policy.encoded(policy.FIXTURE)),
        }

    def verify(self) -> dict[str, Any]:
        data = policy.encoded(self.contract)
        return smoke.verify(data, policy.digest(data), self.root)

    def test_exact_synthetic_contract(self) -> None:
        self.assertEqual(self.verify(), self.contract)

    def test_dependency_source_runtime_and_native_changes_refused(self) -> None:
        for name in (
            "deps/example.py",
            "source/example.py",
            "runtime/python",
            "lib/x86_64-linux-gnu/libc.so.6",
        ):
            target = self.root / name
            original = target.read_bytes()
            target.write_bytes(b"changed")
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify()
            target.write_bytes(original)

    def test_extra_file_and_candidate_replacement_refused(self) -> None:
        (self.root / "deps/extra.py").write_bytes(b"unrecorded")
        with self.assertRaises(ValueError):
            self.verify()
        (self.root / "evidence/candidate.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.verify()

    def test_duplicate_fields_wrong_digest_and_profile_refused(self) -> None:
        duplicate = b'{"schema":"first","schema":"second"}'
        with self.assertRaisesRegex(ValueError, "duplicate"):
            smoke.verify(duplicate, policy.digest(duplicate), self.root)
        with self.assertRaises(ValueError):
            smoke.verify(policy.encoded(self.contract), "0" * 64, self.root)
        self.contract["schema"] = "foreign"
        with self.assertRaises(ValueError):
            self.verify()

    def test_native_path_escape_and_tree_symlink_root_refused(self) -> None:
        self.contract["native"] = {"../../outside": "0" * 64}
        with self.assertRaises(ValueError):
            self.verify()
        (self.root / "alias").symlink_to(self.root / "runtime")
        with self.assertRaises(ValueError):
            smoke.tree(self.root / "alias")
