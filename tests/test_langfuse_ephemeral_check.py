"""Synthetic orchestration negatives, never actual Langfuse qualification."""

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import langfuse_ephemeral_check as check


class WorkflowTests(unittest.TestCase):
    def config(self) -> dict[str, Any]:
        return dict(
            origin="https://fixture.example",
            authorization="Basic synthetic",
            ca_pem="synthetic-unused",
            project_id="project",
            trace_id="trace",
            ca_sha256="a" * 64,
            backend=dict(
                manifest_sha256="b" * 64, config_sha256="c" * 64, version="synthetic"
            ),
        )

    def fake(self, *args: Any) -> Any:
        value = importlib.import_module("test_feedback_projection").HTTP()
        value.origin = self.config()["origin"]
        return value

    def test_synthetic_protocol_flow_and_lost_ack_no_repost(self) -> None:
        with patch.object(check, "LangfuseHTTP", side_effect=self.fake):
            result = check.qualify(self.config())
        self.assertEqual(result["actual_post_entries"], 3)
        self.assertTrue(result["no_blind_repost"])
        self.assertFalse(result["revoked_gold"])
        self.assertEqual(
            result["server_identity"], "runner_evidence_required_not_inferred_from_HTTP"
        )

    def test_bad_readback_cannot_emit_success(self) -> None:
        def bad(*args: Any) -> Any:
            remote = self.fake()
            original = remote.request

            def request(*a: Any, **kw: Any) -> Any:
                result = original(*a, **kw)
                if a[0] == "POST":
                    for row in remote.scores.values():
                        row["projectId"] = "wrong"
                return result

            remote.request = request
            return remote

        with patch.object(check, "LangfuseHTTP", side_effect=bad):
            with self.assertRaises(ValueError):
                check.qualify(self.config())

    def test_private_configuration_symlink_and_hash_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "private.json"
            ca = root / "ca.pem"
            ca.write_text("synthetic CA")
            value = self.config()
            value.pop("ca_pem")
            value.pop("authorization")
            value["schema"] = 1
            value["authorization_file"] = str(root / "authorization")
            value["ca_file"] = str(ca)
            path.write_text(json.dumps(value))
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(ValueError, "CA pin"):
                check.configuration(path)
            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ValueError, "private"):
                check.configuration(path)
            link = root / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                check.read_regular(link, 65536, private=True)
