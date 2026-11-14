"""Explicit retained-feedback selection into existing durable lineage, no training."""

from dataclasses import dataclass
from typing import Any, Callable

from .lineage import Feedback, GoldRule, OutputRevision, SourceRevision, _token, digest
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
    db: Any = store
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
    batch = (tuple(sources), tuple(outputs), tuple(feedback), rules, seed)
    batch_sha = digest(encode(batch))

    receipt = {
        "version": "retained_feedback_collection.v1",
        "batch_sha256": batch_sha,
        "dependency_commit": dependency.pin["commit"],
        "selected": tuple(receipts),
        "collection_ref": collection_ref,
        "training_ref": training_ref,
        "scope": "exact_batch_only_not_execution_authority",
    }
    receipt_sha = digest(encode(receipt))

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
        )
    )
