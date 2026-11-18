"""Actual signed synthetic lineage batch guards; no framework or network."""

import importlib
import unittest
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from refinery.dataset_batch import Selection, capture, read


class BatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.h: Any = importlib.import_module(
            "test_dataset_materialization"
        ).MaterializationTests()
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.item = self.h.add()
        self.selections = (Selection(self.item["item_id"], self.h.source),)
        self.kwargs = dict(
            purpose={"train": "optimization", "heldout": "evaluation"}[
                self.item["split"]
            ],
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )

    def capture(self) -> Any:
        return capture(self.h.registry, self.selections, **self.kwargs)

    def test_exact_repeat_and_changed_manifest_denies(self) -> None:
        manifest, contents = self.capture()
        self.assertEqual(self.capture(), (manifest, contents))
        self.assertEqual(
            read(manifest, self.h.registry, self.selections, **self.kwargs), contents
        )
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            read(manifest + b" ", self.h.registry, self.selections, **self.kwargs)
        self.assertNotIn(contents[0][1], manifest)

    def test_manifest_is_not_current_permission(self) -> None:
        manifest, _ = self.capture()
        self.h.approved = False
        with self.assertRaises(ValueError):
            read(manifest, self.h.registry, self.selections, **self.kwargs)

    def test_withdrawn_item_denies_without_returning_content(self) -> None:
        manifest, _ = self.capture()
        self.h.registry.withdraw(
            self.item["item_id"],
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        with self.assertRaises(ValueError):
            read(manifest, self.h.registry, self.selections, **self.kwargs)

    def test_duplicate_empty_and_wrong_purpose_denied(self) -> None:
        for selections in ((), self.selections * 2):
            with self.assertRaises(ValueError):
                capture(self.h.registry, selections, **self.kwargs)
        with self.assertRaises(ValueError):
            capture(
                self.h.registry, self.selections, **dict(self.kwargs, purpose="export")
            )

    def test_source_rebinding_denies(self) -> None:
        manifest, _ = self.capture()
        denied = replace(
            self.h.source, use=replace(self.h.source.use, approve_use=lambda _: False)
        )
        with self.assertRaises(ValueError):
            read(
                manifest,
                self.h.registry,
                (Selection(self.item["item_id"], denied),),
                **self.kwargs,
            )

    def test_final_fence_catches_post_read_local_withdrawal(self) -> None:
        original = self.h.registry.read

        def altered(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            self.h.registry.withdraw(
                self.item["item_id"],
                approve=self.h.approve,
                permission_ref="explicit-test-grant",
            )
            return result

        with patch.object(self.h.registry, "read", side_effect=altered):
            with self.assertRaises(ValueError):
                self.capture()

    def test_final_fence_detects_target_invalidation_after_read(self) -> None:
        original = self.h.registry.read

        def altered(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            self.h.h.h.invalidate_target("message_deleted")
            return result

        with patch.object(self.h.registry, "read", side_effect=altered):
            with self.assertRaisesRegex(ValueError, "batch feedback changed"):
                self.capture()

    def test_final_fence_detects_cohort_enrollment_after_read(self) -> None:
        from refinery.evaluation import Halt

        original = self.h.registry.read

        def altered(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            self.h.enroll()
            return result

        with patch.object(self.h.registry, "read", side_effect=altered):
            with self.assertRaises(Halt):
                self.capture()
