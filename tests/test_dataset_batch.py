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

    def second(self, *, same_input: bool = False) -> Selection:
        from refinery.dataset_materialization import prepare
        from refinery.feedback_projection import RetainedSource
        from refinery.feedback_use import RetainedFeedbackUse

        h: Any = importlib.import_module(
            "test_thumbs_up_collection"
        ).ReactionCollectionTests()
        h.actor_id = "USECOND"
        h.task_text = "Synthetic task" if same_input else "Second signed task"
        h.setUp()
        self.addCleanup(h.doCleanups)
        selected = h.select()
        use = RetainedFeedbackUse(
            h.dep,
            h.app.store,
            verify_material=lambda target, material: target == h.target
            and material == h.material,
            approve_use=lambda _: True,
        )
        for number in range(100):
            snapshot = h.collect(selected, seed=f"batch-{number}")
            source = RetainedSource(
                h.store,
                snapshot,
                selected,
                use,
                lambda key, destination: dict(self.h.h.authority),
            )
            if prepare(source, "synthetic").descriptor["split"] == self.item["split"]:
                break
        else:
            self.fail("matching synthetic split unavailable")
        item = self.h.registry.materialize(
            source,
            "synthetic",
            approve=self.h.approve,
            permission_ref="explicit-test-grant",
        )
        return Selection(item["item_id"], source)

    def test_two_items_reordered_have_same_manifest_and_content_order(self) -> None:
        second = self.second()
        selections = self.selections + (second,)
        left = capture(self.h.registry, selections, **self.kwargs)
        right = capture(self.h.registry, tuple(reversed(selections)), **self.kwargs)
        self.assertEqual(left, right)
        self.assertEqual(len(left[1]), 2)
        self.assertEqual(
            {row[0] for row in left[1]}, {"Synthetic task", "Second signed task"}
        )

    def test_later_item_permission_withdraws_earlier_item(self) -> None:
        second = self.second()
        selections = tuple(sorted(self.selections + (second,), key=lambda s: s.item_id))
        calls = []

        def approve(descriptor: Any, purpose: str, reference: str) -> bool:
            calls.append(descriptor["item_id"])
            if descriptor["item_id"] == selections[1].item_id:
                self.h.registry.withdraw(
                    selections[0].item_id,
                    approve=self.h.approve,
                    permission_ref="explicit-test-grant",
                )
            return self.h.approve(descriptor, purpose, reference) is True

        with self.assertRaises(ValueError):
            capture(self.h.registry, selections, **dict(self.kwargs, approve=approve))
        self.assertEqual(
            calls, [selections[0].item_id] * 2 + [selections[1].item_id] * 2
        )

    def test_two_independent_occurrences_with_same_input_are_denied(self) -> None:
        second = self.second(same_input=True)
        with self.assertRaisesRegex(ValueError, "duplicate batch occurrence or input"):
            capture(self.h.registry, self.selections + (second,), **self.kwargs)
