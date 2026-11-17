"""Explicit retained-feedback selection into existing durable lineage, no training."""

from dataclasses import dataclass
from typing import Any, Callable

from .lineage import Feedback, GoldRule, OutputRevision, SourceRevision, _token, digest
from .source_feedback import SourceFeedbackGuard, require_no_guarded_identity
from .storage import VERSION, Store, pack
from .tether_dependency import TetherDependency


@dataclass(frozen=True)
class SelectedFeedback:
    namespace: str
    feedback_id: str
    revision: int
    event_sha256: str
    rule_id: str


@dataclass(frozen=True)
class Material:
    source: SourceRevision
    output: OutputRevision
    provenance_ref: str


def collect(
    store: Store,
    dependency: TetherDependency,
    tether_store: Any,
    selections: tuple[SelectedFeedback, ...],
    rules: tuple[GoldRule, ...],
    *,
    seed: str,
    collection_ref: str,
    training_ref: str,
    resolve_material: Callable[[dict[str, Any]], Material | None] | None = None,
    verify_material: Callable[[dict[str, Any], Material], bool] | None = None,
    approve_collection: Callable[[str, str], bool] | None = None,
    approve_training: Callable[[str, str], bool] | None = None,
    source_guard: SourceFeedbackGuard | None = None,
    prior_snapshot_sha256: str | None = None,
) -> str:
    """Both approvals bind exact lineage batch; unchanged evidence checked at commit.

    This persists a dataset only. It never executes training/evaluation, grants
    future permission or retroactively authorizes old immutable snapshots.
    """
    if any(
        callback is None
        for callback in (
            resolve_material,
            verify_material,
            approve_collection,
            approve_training,
        )
    ):
        raise ValueError("explicit collection adapters unavailable")
    if type(selections) is not tuple or not 1 <= len(selections) <= 100:
        raise ValueError("bounded explicit selection required")
    dependency.check(tether_store)
    if source_guard is not None and (
        type(source_guard) is not SourceFeedbackGuard
        or source_guard.dependency is not dependency
    ):
        raise ValueError("unverified source guard")
    db: Any = store
    prior: Any = None
    prior_receipt: Any = None
    prior_bytes = prior_receipt_bytes = None
    if prior_snapshot_sha256 is not None:
        # Explicit revalidation, never a receipt rewrite or inherited consent.
        db.lineage(prior_snapshot_sha256)
        prior = db.get("lineage", prior_snapshot_sha256)
        prior_bytes = pack(prior)
        prior_receipt = db.get("feedback_collection", prior["collection_evidence"])
        prior_receipt_bytes = pack(prior_receipt)
        if (
            digest(prior_bytes) != prior_snapshot_sha256
            or digest(prior_receipt_bytes) != prior["collection_evidence"]
            or prior_receipt.get("dependency_commit")
            != "c3fbfb9ff5ed0483e522c93268ced9e40a6cb79d"
            or dependency.pin["commit"] != "ae5ac216b45ca413283e4200c0a4a61054998b58"
        ):
            raise ValueError("unsupported feedback revalidation transition")
    resolve: Any = resolve_material
    verify: Any = verify_material
    collection: Any = approve_collection
    training: Any = approve_training
    encode: Any = pack
    canonical: Any = dependency.modules["tether.admission"].canonical
    for value in (seed, collection_ref, training_ref):
        _token(value)
    sources, outputs, feedback, receipts = [], [], [], []
    originals: dict[tuple[str, str], bytes] = {}
    actors: dict[str, str] = {}
    seen = set()
    materials: list[tuple[dict[str, Any], Material]] = []
    source_observations: list[dict[str, Any]] = []
    for selected in selections:
        if (
            type(selected) is not SelectedFeedback
            or type(selected.revision) is not int
            or not 1 <= selected.revision <= 2**64 - 1
        ):
            raise ValueError("invalid selection")
        for value in (selected.namespace, selected.feedback_id, selected.rule_id):
            _token(value)
        key = (selected.namespace, selected.feedback_id)
        if key in seen:
            raise ValueError("duplicate feedback selection")
        seen.add(key)
        evidence = tether_store.evidence(*key)
        originals[key] = canonical(evidence)
        matches = [
            r
            for r in evidence["revisions"]
            if r["event"]["revision"] == selected.revision
            and r["event_sha256"] == selected.event_sha256
        ]
        if len(matches) != 1:
            raise ValueError("selected immutable event unavailable")
        retained = matches[0]
        event = retained["event"]
        if event["kind"] == "delete":
            raise ValueError("select original judgment, not tombstone")
        target = event["target"]
        try:
            material = resolve(dict(target))
            allowed = (
                type(material) is Material and verify(dict(target), material) is True
            )
        except Exception:
            raise ValueError("source material verification unavailable") from None
        if not allowed:
            raise ValueError("source material denied")
        if (
            type(material.source) is not SourceRevision
            or type(material.output) is not OutputRevision
        ):
            raise ValueError("source material type")
        validated: Any = material
        validated.source.__post_init__()
        validated.output.__post_init__()
        _token(material.provenance_ref)
        source, output = material.source, material.output
        if (output.output_id, output.revision, output.sha256) != (
            target["artifact_id"],
            str(target["artifact_revision"]),
            target["artifact_sha256"],
        ) or (output.source_id, output.source_revision, output.source_sha256) != (
            source.source_id,
            source.revision,
            source.sha256,
        ):
            raise ValueError("exact source/output revision mismatch")
        active = (
            evidence["state"] == "active"
            and evidence["head_revision"] == selected.revision
        )
        source_observation = None
        if source_guard is not None:
            source_observation = source_guard.observe(target, material)
            source_observations.append(source_observation)
            active = active and source_observation["state"] == "unknown"
        observed = Feedback(
            digest(
                encode(
                    (
                        selected.namespace,
                        selected.feedback_id,
                        selected.revision,
                        selected.event_sha256,
                    )
                )
            ),
            event["actor_id"],
            output.output_id,
            output.revision,
            output.sha256,
            selected.rule_id,
            event["judgment"],
            event["correction"].encode() if event["judgment"] == "correction" else None,
            target_available=active,
        )
        sources.append(source)
        outputs.append(output)
        feedback.append(observed)
        actors[digest(encode(observed))] = event["actor_id"]
        materials.append((target, material))
        receipts.append(
            {
                "selection": vars(selected),
                "evidence_sha256": digest(originals[key]),
                "state": evidence["state"],
                "head_revision": evidence["head_revision"],
                "material_ref": material.provenance_ref,
                "target": target,
            }
        )
        if source_guard is not None:
            receipts[-1]["source_observation"] = source_observation
    batch = (tuple(sources), tuple(outputs), tuple(feedback), rules, seed)
    batch_sha = digest(encode(batch))

    receipt = {
        "version": "retained_feedback_collection.v2"
        if source_guard is not None
        else "retained_feedback_collection.v1",
        "batch_sha256": batch_sha,
        "dependency_commit": dependency.pin["commit"],
        "selected": tuple(receipts),
        "collection_ref": collection_ref,
        "training_ref": training_ref,
        "scope": "exact_batch_only_not_execution_authority",
    }
    receipt_sha = digest(encode(receipt))
    expected_snapshot = None
    if prior is not None:
        # Only provenance and fresh approval references may change. Selection,
        # source fences, dataset exclusions and locked partitions cannot drift.
        historical = dict(prior_receipt)
        for field in ("dependency_commit", "collection_ref", "training_ref"):
            historical[field] = receipt[field]
        if historical != receipt or encode(prior["batch"]) != encode(batch):
            raise ValueError("feedback revalidation content changed")
        expected_snapshot = dict(prior, collection_evidence=receipt_sha)

    def permissions() -> None:
        try:
            allowed = (
                collection(batch_sha, collection_ref) is True
                and training(batch_sha, training_ref) is True
            )
        except Exception:
            allowed = False
        if not allowed:
            raise ValueError("collection or training consent denied")

    def check_evidence() -> None:
        dependency.check(tether_store)
        if source_guard is None:
            require_no_guarded_identity(db, set(originals))
        if source_guard is not None and any(
            source_guard.observe(target, material) != observation
            for (target, material), observation in zip(materials, source_observations)
        ):
            raise ValueError("source changed during collection")
        # Resolve every source before the final callback-free observations. A
        # later resolver can mutate an earlier source or the feedback ledger.
        dependency.check(tether_store)
        if source_guard is not None and any(
            source_guard.reread(observation) != observation
            for observation in source_observations
        ):
            raise ValueError("source changed during collection")
        if any(
            canonical(tether_store.evidence(*key)) != raw
            for key, raw in originals.items()
        ):
            raise ValueError("feedback changed during collection")

    def recheck() -> None:
        check_evidence()
        try:
            valid = all(
                verify(dict(target), material) is True for target, material in materials
            )
        except Exception:
            valid = False
        if not valid:
            raise ValueError("source material verification revoked")
        permissions()
        # Trusted callbacks may update another store. Read again after all callbacks,
        # immediately before the caller inserts provenance in this transaction.
        check_evidence()

    permissions()

    def checkpoint(point: str) -> None:
        if point == "before_feedback_commit":
            recheck()
            if prior is not None:
                if (
                    pack(db.get("lineage", prior_snapshot_sha256)) != prior_bytes
                    or pack(db.get("feedback_collection", prior["collection_evidence"]))
                    != prior_receipt_bytes
                ):
                    raise ValueError("historical feedback changed during revalidation")
                expected_sha = digest(encode(expected_snapshot))
                if encode(db.get("lineage", expected_sha)) != encode(expected_snapshot):
                    raise ValueError("feedback revalidation dataset changed")
                link = {
                    "version": "feedback_revalidation.v1",
                    "prior_snapshot_sha256": prior_snapshot_sha256,
                    "snapshot_sha256": expected_sha,
                    "prior_receipt_sha256": prior["collection_evidence"],
                    "receipt_sha256": receipt_sha,
                    "scope": "fresh_collection_only_not_use_or_execution_authority",
                }
                db.put(db.db, "feedback_revalidation", digest(encode(link)), link)
            db.put(db.db, "feedback_collection", receipt_sha, receipt)

    def permit_batch(sha: str, ref: str) -> bool:
        if sha != batch_sha or ref != receipt_sha:
            return False
        permissions()
        return True

    return str(
        db.commit_lineage(
            VERSION,
            *batch[:4],
            seed=seed,
            collection_evidence=receipt_sha,
            verify_collection=permit_batch,
            verify_actor=lambda f: actors.get(digest(encode(f))),
            checkpoint=checkpoint,
            expected_snapshot_sha256=digest(encode(expected_snapshot))
            if expected_snapshot is not None
            else None,
        )
    )
