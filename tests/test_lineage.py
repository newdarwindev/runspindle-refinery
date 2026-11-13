"""Synthetic tests of real lineage functions; no provider or upstream evidence."""
from dataclasses import FrozenInstanceError, replace
import socket
import unittest
from unittest.mock import patch

from refinery.lineage import (
    Dataset, Feedback, GoldRule, MAX_RECORDS, OutputRevision, Quarantine,
    SourceRevision, build_dataset, digest, lock_split,
)


def fixture(revision="1", *, family="thread-1", source_id="source-1", output_id="output-1"):
    source = SourceRevision(source_id, revision, family, b"Invented synthetic input", digest(b"Invented synthetic input"))
    body = ("Invented synthetic output " + revision).encode()
    output = OutputRevision(output_id, revision, "summary", source_id, revision, source.sha256, body, digest(body))
    feedback = Feedback("feedback-" + output_id + "-" + revision, "actor-1", output_id, revision, output.sha256, "summary-v1", "positive")
    return source, output, feedback


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.net = patch.object(socket, "socket", side_effect=AssertionError("outbound forbidden"))
        self.socket = self.net.start()
        self.addCleanup(self.net.stop)
        self.addCleanup(self.socket.assert_not_called)
        self.source, self.output, self.feedback = fixture()
        self.rule = GoldRule("summary", "summary-v1", ("positive", "correction"))
        self.verified = []

    def verify(self, feedback):
        self.verified.append(feedback.feedback_id)
        return "actor-1" if feedback.actor_id == "actor-1" else None

    def build(self, *, sources=None, outputs=None, feedback=None, rules=None):
        return build_dataset(sources if sources is not None else [self.source],
                             outputs if outputs is not None else [self.output],
                             feedback if feedback is not None else [self.feedback],
                             rules if rules is not None else [self.rule], verify_actor=self.verify)

    def reason(self, dataset, reason):
        self.assertEqual(dataset.examples, ())
        self.assertIn(reason, dataset.excluded_counts)

    def test_exact_bytes_and_immutable_lineage(self):
        result = self.build()
        example = result.examples[0]
        self.assertEqual(example.gold, self.output.content)
        self.assertEqual(example.source.sha256, digest(self.source.content))
        self.assertEqual(example.feedback_ids, (self.feedback.feedback_id,))
        self.assertEqual(example.actor_ids, ("actor-1",))
        self.assertEqual(self.verified, [self.feedback.feedback_id])
        with self.assertRaises(FrozenInstanceError):
            example.gold = b"changed"

    def test_default_authority_is_deny(self):
        result = build_dataset([self.source], [self.output], [self.feedback], [self.rule])
        self.reason(result, "unverified_actor")

    def test_forged_actor_and_unavailable_verifier_are_denied(self):
        self.reason(self.build(feedback=[replace(self.feedback, actor_id="actor-forged")]), "unverified_actor")
        def unavailable(_):
            raise TimeoutError("synthetic outage")
        self.reason(build_dataset([self.source], [self.output], [self.feedback], [self.rule], verify_actor=unavailable), "unverified_actor")

    def test_feedback_on_old_revision_never_labels_latest(self):
        old_source, old_output, old_feedback = fixture()
        new_source, new_output, _ = fixture("2")
        result = self.build(sources=[old_source, new_source], outputs=[old_output, new_output], feedback=[old_feedback])
        self.assertEqual([e.output.revision for e in result.examples], ["1"])
        self.reason(self.build(sources=[new_source], outputs=[new_output], feedback=[old_feedback]), "missing_or_changed_output")

    def test_wrong_output_hash_or_source_hash_quarantined(self):
        self.reason(self.build(feedback=[replace(self.feedback, output_sha256=digest(b"altered"))]), "missing_or_changed_output")
        self.reason(self.build(outputs=[replace(self.output, source_sha256=digest(b"altered"))]), "missing_or_changed_source")

    def test_incomplete_and_missing_context_quarantined(self):
        for sources, outputs in [([], [self.output]), ([replace(self.source, complete=False)], [self.output]),
                                 ([self.source], [replace(self.output, complete=False)])]:
            result = self.build(sources=sources, outputs=outputs)
            self.assertFalse(result.examples)
            self.assertEqual(len(result.quarantine), 1)

    def test_edited_or_deleted_unavailable_target_quarantined(self):
        self.reason(self.build(feedback=[replace(self.feedback, target_available=False)]), "unavailable_target")

    def test_task_rule_is_explicit(self):
        self.reason(self.build(rules=[]), "unknown_gold_rule")
        wrong_task = GoldRule("other-task", "summary-v1", ("positive",))
        self.reason(self.build(rules=[wrong_task]), "unknown_gold_rule")
        correction = replace(self.feedback, kind="correction", correction=b"A synthetic correction")
        self.reason(self.build(feedback=[correction], rules=[replace(self.rule, kinds=("positive",))]), "unsupported_gold_kind")

    def test_correction_retains_exact_original_and_corrected_bytes(self):
        correction = replace(self.feedback, kind="correction", correction=b"A synthetic correction")
        result = self.build(feedback=[correction])
        self.assertEqual(result.examples[0].output, self.output)
        self.assertEqual(result.examples[0].gold, correction.correction)

    def test_positive_negative_conflict_excludes_entire_target(self):
        negative = replace(self.feedback, feedback_id="negative-1", kind="negative")
        result = self.build(feedback=[self.feedback, negative])
        self.reason(result, "negative_feedback")
        self.assertEqual(result.excluded_counts, {"conflicting_feedback": 1, "negative_feedback": 1})

    def test_conflicting_corrections_excluded(self):
        first = replace(self.feedback, kind="correction", correction=b"Synthetic one")
        second = replace(first, feedback_id="feedback-second", correction=b"Synthetic two")
        result = self.build(feedback=[first, second])
        self.reason(result, "conflicting_feedback")
        self.assertEqual(result.excluded_counts["conflicting_feedback"], 2)

    def test_duplicate_delivery_counted_once(self):
        result = self.build(feedback=[self.feedback, self.feedback])
        self.assertEqual(result.observed_feedback, 2)
        self.assertEqual(result.duplicate_feedback, 1)
        self.assertEqual(len(result.examples), 1)
        self.assertEqual(self.verified, [self.feedback.feedback_id])

    def test_conflicting_feedback_id_blocks_other_gold_on_same_target(self):
        corrupt = replace(self.feedback, kind="negative")
        other = replace(self.feedback, feedback_id="other-feedback")
        result = self.build(feedback=[self.feedback, corrupt, other])
        self.reason(result, "conflicting_feedback_id")
        self.assertEqual(result.excluded_counts["conflicting_feedback"], 1)
        self.assertEqual(result.observed_feedback, len(result.quarantine) + result.duplicate_feedback)

    def test_conflicting_id_with_unverified_actor_is_conservatively_excluded(self):
        forged = replace(self.feedback, actor_id="forged-actor", kind="negative")
        other = replace(self.feedback, feedback_id="other-valid-feedback")
        self.reason(self.build(feedback=[self.feedback, forged, other]), "conflicting_feedback_id")

    def test_conflicting_immutable_revision_rejected(self):
        for sources, outputs in [([self.source, replace(self.source, family_id="other-family")], [self.output]),
                                 ([self.source], [self.output, replace(self.output, task_type="other-task")])]:
            with self.assertRaisesRegex(ValueError, "conflicting immutable"):
                self.build(sources=sources, outputs=outputs)

    def test_related_revisions_cannot_change_family(self):
        source2, output2, feedback2 = fixture("2", family="different-family")
        with self.assertRaisesRegex(ValueError, "share one family"):
            self.build(sources=[self.source, source2], outputs=[self.output, output2], feedback=[self.feedback, feedback2])

    def test_grouped_split_deterministic_and_input_order_independent(self):
        triples = [fixture(str(i), family="thread-" + str(i // 2), source_id="source-" + str(i // 2), output_id="out-" + str(i // 2)) for i in range(20)]
        sources, outputs, feedback = map(list, zip(*triples))
        a = self.build(sources=sources, outputs=outputs, feedback=feedback)
        b = self.build(sources=reversed(sources), outputs=reversed(outputs), feedback=reversed(feedback))
        split = lock_split(a, seed="seed-1")
        self.assertEqual(split, lock_split(b, seed="seed-1"))
        self.assertEqual(len(split.assignments), 10)
        self.assertEqual({p for _, p in split.assignments}, {"train", "heldout"})
        for e in a.examples:
            self.assertIn(split.partition(e.source.family_id), {"train", "heldout"})
        split.verify(a)

    def test_lock_detects_dataset_mutation_and_exclusion_change(self):
        dataset = self.build()
        split = lock_split(dataset, seed="seed-1")
        bad_example = replace(dataset.examples[0], gold=b"deliberately mutated")
        for mutated in [replace(dataset, examples=(bad_example,)),
                        replace(dataset, quarantine=(Quarantine("excluded", "incomplete_context"),))]:
            with self.assertRaisesRegex(ValueError, "changed"):
                split.verify(mutated)

    def test_deliberate_split_assignment_mutation_fails_independent_verification(self):
        dataset = self.build()
        split = lock_split(dataset, seed="seed-1")
        changed = replace(split, assignments=(("thread-1", "forged-partition"),))
        with self.assertRaises(ValueError):
            changed.verify(dataset)

    def test_empty_dataset_is_explicit_and_deterministic(self):
        result = self.build(sources=[], outputs=[], feedback=[])
        self.assertEqual(result, Dataset((), (), 0, 0))
        self.assertEqual(lock_split(result, seed="seed-1").assignments, ())

    def test_bounds_and_invalid_bytes_fail_before_verifier(self):
        for feedback in [[self.feedback] * (MAX_RECORDS + 1), [object()]]:
            with self.assertRaises(ValueError):
                self.build(feedback=feedback)
        self.assertEqual(self.verified, [])
        for content in [bytearray(b"mutable"), b"x" * 1_048_577]:
            with self.assertRaises(ValueError):
                replace(self.source, content=content)
        with self.assertRaises(ValueError):
            replace(self.output, content=b"tampered")
        with self.assertRaises(ValueError):
            replace(self.feedback, output_sha256="not-a-hash")

    def test_gold_data_has_no_execution_or_provider_capabilities(self):
        example = self.build().examples[0]
        self.assertEqual(set(vars(example)), {"source", "output", "gold", "rule_id", "feedback"})
        from refinery import activate
        with self.assertRaises(NotImplementedError):
            activate()


if __name__ == "__main__":
    unittest.main()
