"""Automatic label policy requires independently authenticated exact reaction evidence."""

import importlib
import unittest
from dataclasses import replace
from typing import Any

from refinery.lineage import THUMBS_UP_GOLD_RULE, GoldRule, build_dataset

fixture: Any = importlib.import_module("test_lineage").fixture


class ThumbsUpGoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source, self.output, positive = fixture()
        self.feedback = replace(positive, kind="thumbs_up", rule_id=THUMBS_UP_GOLD_RULE)

    def build(self, feedback: Any = None, rules: Any = (), **kwargs: Any) -> Any:
        events = feedback or (self.feedback,)
        # Stand-in immutable observation authenticator binds all feedback fields,
        # not actor_id alone. No connector/authentication acceptance claim.
        return build_dataset(
            (self.source,),
            (self.output,),
            events,
            rules,
            verify_actor=kwargs.get(
                "verify_actor", lambda f: f.actor_id if f in events else None
            ),
        )

    def test_thumb_alone_automatically_promotes_exact_output_without_rule(self) -> None:
        data = self.build()
        self.assertEqual(data.examples[0].gold, self.output.content)
        self.assertEqual(data.examples[0].rule_id, THUMBS_UP_GOLD_RULE)
        self.assertEqual(data.examples[0].feedback, (self.feedback,))
        self.assertEqual(data.quarantine, ())

    def test_task_whitelist_does_not_override_verified_thumb(self) -> None:
        data = self.build(
            rules=(GoldRule(self.output.task_type, "correction-only", ("correction",)),)
        )
        self.assertEqual(data.examples[0].gold, self.output.content)

    def test_unverified_or_missing_exact_target_still_denied(self) -> None:
        self.assertFalse(self.build(verify_actor=None).examples)
        self.assertFalse(self.build(verify_actor=lambda f: None).examples)
        for field, value in [
            ("target_available", False),
            ("output_revision", "missing"),
            ("output_sha256", "a" * 64),
        ]:
            event = replace(self.feedback, **{field: value})
            self.assertFalse(self.build((event,)).examples)

    def test_arbitrary_positive_is_not_automatic_thumb(self) -> None:
        event = replace(self.feedback, kind="positive")
        self.assertFalse(self.build((event,)).examples)
        with self.assertRaises(ValueError):
            replace(self.feedback, rule_id="other")
        with self.assertRaises(ValueError):
            replace(self.feedback, correction=b"new text")

    def test_verified_negative_and_conflicting_correction_are_not_erased(self) -> None:
        for kind, correction in [("negative", None), ("correction", b"different")]:
            other = replace(
                self.feedback,
                feedback_id="other",
                rule_id="review",
                kind=kind,
                correction=correction,
            )
            self.assertFalse(
                self.build(
                    (self.feedback, other),
                    (
                        GoldRule(
                            self.output.task_type, "review", ("positive", "correction")
                        ),
                    ),
                ).examples
            )

    def test_same_gold_existing_positive_does_not_conflict_with_automatic_rule(
        self,
    ) -> None:
        other = replace(
            self.feedback, feedback_id="other", rule_id="review", kind="positive"
        )
        result = self.build(
            (self.feedback, other),
            (GoldRule(self.output.task_type, "review", ("positive",)),),
        )
        self.assertEqual(len(result.examples), 1)
        self.assertEqual(result.examples[0].rule_id, THUMBS_UP_GOLD_RULE)
        self.assertEqual(len(result.examples[0].feedback), 2)

    def test_duplicate_delivery_and_distinct_actors_retain_attribution(self) -> None:
        other = replace(self.feedback, feedback_id="second-event", actor_id="actor-2")
        result = self.build((self.feedback, self.feedback, other))
        self.assertEqual(result.duplicate_feedback, 1)
        self.assertEqual(len(result.examples), 1)
        self.assertEqual(result.examples[0].actor_ids, ("actor-1", "actor-2"))
        self.assertEqual(len(result.examples[0].feedback), 2)

    def test_any_task_type_preserves_exact_bytes_and_family(self) -> None:
        self.output = replace(self.output, task_type="unconfigured-task")
        result = self.build()
        self.assertEqual(result.examples[0].gold, self.output.content)
        self.assertEqual(result.examples[0].source.family_id, self.source.family_id)

    def test_durable_snapshot_reconstructs_automatic_label_without_rules(self) -> None:
        import tempfile
        from pathlib import Path

        from refinery.lineage import digest
        from refinery.storage import VERSION, Store, pack

        with (
            tempfile.TemporaryDirectory() as directory,
            Store(Path(directory) / "gold.sqlite") as store,
        ):
            observed = digest(pack(self.feedback))
            snapshot = store.commit_lineage(
                VERSION,
                (self.source,),
                (self.output,),
                (self.feedback,),
                (),
                seed="gold-seed",
                collection_evidence="explicit-synthetic-consent",
                verify_collection=lambda sha, evidence: evidence
                == "explicit-synthetic-consent",
                verify_actor=lambda f: f.actor_id
                if digest(pack(f)) == observed
                else None,
            )
            data, split = store.lineage(snapshot)
            self.assertEqual(data.examples[0].gold, self.output.content)
            self.assertEqual(data.examples[0].rule_id, THUMBS_UP_GOLD_RULE)
            self.assertIn(split.partition(self.source.family_id), ("train", "heldout"))
