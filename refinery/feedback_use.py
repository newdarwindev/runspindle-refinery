"""Use-time checks of retained feedback, not external consent authentication.

Every check observes pinned Tether state anew. Component stores and injected
callbacks are trusted; these reads are not cross-database atomic revocation.
"""

from dataclasses import dataclass
from typing import Any, Callable

from . import lineage as lin
from .evaluation import Halt
from .feedback_collection import Material, SelectedFeedback
from .source_feedback import SourceFeedbackGuard, require_no_guarded_identity
from .storage import Store, pack
from .tether_dependency import TetherDependency

SYNTHETIC_FIXTURE_REFERENCE = "synthetic-consent"


class UseDenied(Halt):
    """No retained bytes may be used; existing reservations stay charged."""


@dataclass(frozen=True)
class UseRequest:
    snapshot_sha256: str
    batch_sha256: str
    collection_receipt_sha256: str
    purpose: str
    configuration_sha256: str
    run_sha256: str


@dataclass(frozen=True)
class RetainedFeedbackUse:
    dependency: TetherDependency
    tether_store: Any
    verify_material: Callable[[dict[str, Any], Material], bool] | None = None
    approve_use: Callable[[UseRequest], bool] | None = None
    source_guard: SourceFeedbackGuard | None = None

    def verify(self, store: Store, request: UseRequest) -> None:
        """Recheck the exact receipt, material and current permission, fail closed."""
        db: Any = store
        encode: Any = pack
        if self.verify_material is None or self.approve_use is None:
            raise ValueError("use authority unavailable")
        self.dependency.check(self.tether_store)
        snapshot = db.get("lineage", request.snapshot_sha256)
        receipt = db.get("feedback_collection", request.collection_receipt_sha256)
        if (
            lin.digest(encode(snapshot)) != request.snapshot_sha256
            or snapshot["collection_evidence"] != request.collection_receipt_sha256
            or snapshot["batch_sha256"] != request.batch_sha256
            or lin.digest(encode(snapshot["batch"])) != request.batch_sha256
            or lin.digest(encode(receipt)) != request.collection_receipt_sha256
            or set(receipt)
            != {
                "version",
                "batch_sha256",
                "dependency_commit",
                "selected",
                "collection_ref",
                "training_ref",
                "scope",
            }
            or receipt["version"]
            not in (
                "retained_feedback_collection.v1",
                "retained_feedback_collection.v2",
            )
            or receipt["scope"] != "exact_batch_only_not_execution_authority"
            or receipt["dependency_commit"] != self.dependency.pin["commit"]
            or receipt["batch_sha256"] != request.batch_sha256
        ):
            raise ValueError("collection association changed")
        guarded = receipt["version"] == "retained_feedback_collection.v2"
        if guarded and (
            type(self.source_guard) is not SourceFeedbackGuard
            or self.source_guard.dependency is not self.dependency
        ):
            raise ValueError("source use guard required")
        if not guarded and self.source_guard is not None:
            raise ValueError("historical receipt has no source binding")
        for field in ("collection_ref", "training_ref"):
            lin._token(receipt[field])
        sources, outputs, feedback, rules, seed = snapshot["batch"]
        selected = receipt["selected"]
        if (
            type(selected) is not tuple
            or not 1 <= len(selected) <= 100
            or any(type(v) is not tuple for v in (sources, outputs, feedback, rules))
            or not len(sources) == len(outputs) == len(feedback) == len(selected)
            or snapshot["split"].seed != seed
        ):
            raise ValueError("collection batch changed")
        originals: dict[tuple[str, str], bytes] = {}
        materials = []
        actors = {}
        canonical: Any = self.dependency.modules["tether.admission"].canonical
        for row, source, output, observed in zip(selected, sources, outputs, feedback):
            if set(row) != {
                "selection",
                "evidence_sha256",
                "state",
                "head_revision",
                "material_ref",
                "target",
            } | ({"source_observation"} if guarded else set()):
                raise ValueError("invalid selection receipt")
            selection = SelectedFeedback(**row["selection"])
            if (
                type(selection.revision) is not int
                or not 1 <= selection.revision <= 2**64 - 1
            ):
                raise ValueError("invalid selected revision")
            for value in (
                selection.namespace,
                selection.feedback_id,
                selection.rule_id,
                row["material_ref"],
            ):
                lin._token(value)
            lin._hash(selection.event_sha256)
            key = (selection.namespace, selection.feedback_id)
            if key in originals:
                raise ValueError("duplicate selection")
            evidence = self.tether_store.evidence(*key)
            originals[key] = canonical(evidence)
            if (
                lin.digest(originals[key]) != row["evidence_sha256"]
                or evidence["state"] != row["state"]
                or evidence["state"] != "active"
                or evidence["head_revision"] != row["head_revision"]
                or evidence["head_revision"] != selection.revision
            ):
                raise ValueError("feedback no longer current")
            matches = [
                r
                for r in evidence["revisions"]
                if r["event"]["revision"] == selection.revision
                and r["event_sha256"] == selection.event_sha256
            ]
            if len(matches) != 1:
                raise ValueError("selected event missing")
            event = matches[0]["event"]
            target = event["target"]
            if event["kind"] == "delete" or target != row["target"]:
                raise ValueError("selected target changed")
            if (
                type(source) is not lin.SourceRevision
                or type(output) is not lin.OutputRevision
            ):
                raise ValueError("invalid source mapping")
            # Existing lineage dataclasses expose dynamic runtime validators.
            validated_source: Any = source
            validated_output: Any = output
            validated_source.__post_init__()
            validated_output.__post_init__()
            if (output.output_id, output.revision, output.sha256) != (
                target["artifact_id"],
                str(target["artifact_revision"]),
                target["artifact_sha256"],
            ) or (output.source_id, output.source_revision, output.source_sha256) != (
                source.source_id,
                source.revision,
                source.sha256,
            ):
                raise ValueError("source target mismatch")
            expected = lin.Feedback(
                lin.digest(
                    encode(
                        (
                            selection.namespace,
                            selection.feedback_id,
                            selection.revision,
                            selection.event_sha256,
                        )
                    )
                ),
                event["actor_id"],
                output.output_id,
                output.revision,
                output.sha256,
                selection.rule_id,
                event["judgment"],
                event["correction"].encode()
                if event["judgment"] == "correction"
                else None,
                target_available=True,
            )
            if observed != expected:
                raise ValueError("feedback batch mismatch")
            actors[lin.digest(encode(expected))] = expected.actor_id
            materials.append((target, Material(source, output, row["material_ref"])))

        def check_sources() -> None:
            if not guarded:
                require_no_guarded_identity(db, set(originals))
            if guarded:
                if self.source_guard is None:
                    raise ValueError("source use guard unavailable")
                for row, (target, material) in zip(selected, materials):
                    observation = self.source_guard.observe(target, material)
                    if (
                        observation != row["source_observation"]
                        or observation["state"] != "unknown"
                    ):
                        raise ValueError("source no longer eligible")
                # Resolver callbacks for later selections may fence earlier
                # sources. Observe every fence again without invoking callbacks.
                for row in selected:
                    if self.source_guard.reread(row["source_observation"]) != row[
                        "source_observation"
                    ]:
                        raise ValueError("source no longer eligible")

        check_sources()
        expected_dataset = lin.build_dataset(
            sources,
            outputs,
            feedback,
            rules,
            verify_actor=lambda f: actors.get(lin.digest(encode(f))),
        )
        if snapshot["dataset"] != expected_dataset or snapshot[
            "split"
        ] != lin.lock_split(expected_dataset, seed=seed):
            raise ValueError("dataset batch mismatch")
        # A stored collection/training reference is never a new use decision.
        if not all(
            self.verify_material(dict(target), material) is True
            for target, material in materials
        ):
            raise ValueError("source authority denied")
        if self.approve_use(request) is not True:
            raise ValueError("use authority denied")
        # All callbacks precede this last observation, including callbacks that
        # mutate the other SQLite store while returning an affirmative decision.
        check_sources()
        self.dependency.check(self.tether_store)
        if any(
            canonical(self.tether_store.evidence(*key)) != raw
            for key, raw in originals.items()
        ):
            raise ValueError("feedback changed during use verification")


def use_guard(
    store: Store,
    snapshot_sha256: str,
    *,
    purpose: str,
    configuration_sha256: str,
    run_sha256: str,
    retained_use: RetainedFeedbackUse | None,
    synthetic_fixture: bool,
    policy_owner: str | None = None,
) -> Callable[[], None]:
    """Bind a fresh checker, with a separately marked standalone fixture path.

    Missing receipts never fall back to synthetic use. Explicit synthetic use is
    allowed only for the standalone fixture marker, not a collection receipt.
    """
    db: Any = store
    encode: Any = pack

    def check() -> None:
        try:
            snapshot = db.get("lineage", snapshot_sha256)
            if lin.digest(encode(snapshot)) != snapshot_sha256:
                raise ValueError("snapshot changed")
            from .heldout_policy import check as check_holdout

            check_holdout(
                store,
                snapshot["dataset"],
                snapshot["split"],
                purpose=purpose,
                configuration_sha256=configuration_sha256,
                owner=policy_owner,
            )
            reference = snapshot["collection_evidence"]
            if synthetic_fixture is True:
                if retained_use is not None or reference != SYNTHETIC_FIXTURE_REFERENCE:
                    raise ValueError("not a standalone synthetic fixture")
                # Re-batching or changing a split seed cannot relabel known
                # retained identities as standalone synthetic fixtures.
                used = {f.feedback_id for f in snapshot["batch"][2]}
                used.update(
                    f.feedback_id
                    for e in snapshot["dataset"].examples
                    for f in e.feedback
                )
                for (identity,) in db.db.execute(
                    "SELECT identity FROM objects WHERE kind='feedback_collection'"
                ):
                    receipt = db.get("feedback_collection", identity)
                    retained_ids = {
                        lin.digest(
                            encode(
                                tuple(
                                    row["selection"][key]
                                    for key in (
                                        "namespace",
                                        "feedback_id",
                                        "revision",
                                        "event_sha256",
                                    )
                                )
                            )
                        )
                        for row in receipt["selected"]
                    }
                    if (
                        receipt["batch_sha256"] == snapshot["batch_sha256"]
                        or used & retained_ids
                    ):
                        raise ValueError("retained batch is not a standalone fixture")
                check_holdout(
                    store,
                    snapshot["dataset"],
                    snapshot["split"],
                    purpose=purpose,
                    configuration_sha256=configuration_sha256,
                    owner=policy_owner,
                )
                return
            if (
                synthetic_fixture is not False
                or type(retained_use) is not RetainedFeedbackUse
            ):
                raise ValueError("retained use verifier required")
            retained_use.verify(
                store,
                UseRequest(
                    snapshot_sha256,
                    snapshot["batch_sha256"],
                    reference,
                    purpose,
                    configuration_sha256,
                    run_sha256,
                ),
            )
            check_holdout(
                store,
                snapshot["dataset"],
                snapshot["split"],
                purpose=purpose,
                configuration_sha256=configuration_sha256,
                owner=policy_owner,
            )
        except Exception:
            raise UseDenied("retained_feedback_use_denied") from None

    return check
