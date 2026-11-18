"""Current materialized batch authority bound by the durable Session owner.

This is not a generic cohort-owner override or a new execution coordinator.
"""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from typing import Any

from . import dataset_contract as c
from .dataset_batch import Selection, source_fence
from .dataset_materialization import Authority, DatasetRegistry, authorize
from .feedback_projection import RetainedSource
from .feedback_use import RetainedFeedbackUse
from .readonly_view import ReadView


@dataclass(frozen=True)
class AuthorizedBatch:
    manifest: bytes
    registry: DatasetRegistry
    selections: tuple[Selection, ...]
    approve: Authority
    permission_ref: str


class BoundBatch:
    """Owner-thread admission, independent of a stored grant or remote score."""

    def __init__(
        self,
        batch: AuthorizedBatch,
        store: Any,
        snapshot: str,
        retained_use: RetainedFeedbackUse,
    ) -> None:
        if (
            type(batch) is not AuthorizedBatch
            or type(batch.registry) is not DatasetRegistry
        ):
            raise ValueError("exact authorized materialized batch required")
        if type(batch.manifest) is not bytes or not 1 <= len(batch.manifest) <= 262144:
            raise ValueError("bounded immutable manifest required")
        value = json.loads(batch.manifest)
        if (
            c.canonical(value) != batch.manifest
            or set(value) != {"version", "purpose", "items"}
            or value["version"] != "refinery.dataset-batch.v1"
            or value["purpose"] not in {"optimization", "evaluation"}
            or type(value["items"]) is not list
            or not 1 <= len(value["items"]) <= 100
        ):
            raise ValueError("invalid materialized manifest")
        if (
            type(batch.selections) is not tuple
            or len(batch.selections) != len(value["items"])
            or any(
                type(s) is not Selection
                or type(s.source) is not RetainedSource
                or s.source.store is not store
                or s.source.snapshot != snapshot
                or s.source.use is not retained_use
                for s in batch.selections
            )
        ):
            raise ValueError(
                "materialized batch must bind exact Session lineage authority"
            )
        ids = [row["item"]["item_id"] for row in value["items"]]
        if ids != sorted(set(ids)) or ids != sorted(
            s.item_id for s in batch.selections
        ):
            raise ValueError("materialized selection identity changed")
        self.batch, self.value, self.purpose = batch, value, value["purpose"]
        self.manifest = batch.manifest
        self.registry = ReadView(batch.registry, DatasetRegistry)
        dep = retained_use.dependency
        self.feedback = ReadView(
            retained_use.tether_store, dep.modules["tether.feedback"].FeedbackStore
        )
        guard = retained_use.source_guard
        self.source = (
            None
            if guard is None
            else ReadView(
                guard.observation_store,
                dep.modules["tether.source_observation"].SourceObservationStore,
            )
        )
        self.use = retained_use
        self.identity = c.digest(
            c.canonical(
                {
                    "manifest_sha256": c.digest(batch.manifest),
                    "permission_ref_sha256": c.digest(
                        batch.permission_ref.encode("ascii")
                    ),
                    "registry_storage": self.storage_binding(self.registry),
                    "feedback_storage": self.storage_binding(self.feedback),
                    "source_storage": None
                    if self.source is None
                    else self.storage_binding(self.source),
                }
            )
        )

    @staticmethod
    def storage_binding(view: ReadView) -> str:
        # Resume must use the same actual local authorities, not a stale copied
        # registry or feedback database. Only the digest enters ownership bytes.
        return c.digest(
            c.canonical(
                {
                    "path": str(view.path),
                    "device": view.file_identity[0],
                    "inode": view.file_identity[1],
                }
            )
        )

    @contextmanager
    def scope(self) -> Any:
        if self.batch.manifest != self.manifest:
            raise ValueError("materialized manifest changed")
        with ExitStack() as stack:
            registry = stack.enter_context(self.registry.open())
            registry.check()
            feedback = stack.enter_context(self.feedback.open())
            guard = self.use.source_guard
            if guard is not None:
                if self.source is None:
                    raise ValueError("source scope unavailable")
                guard = replace(
                    guard, observation_store=stack.enter_context(self.source.open())
                )
            use = replace(self.use, tether_store=feedback, source_guard=guard)
            selections = tuple(
                Selection(s.item_id, replace(s.source, use=use))
                for s in self.batch.selections
            )
            yield registry, use, selections
            registry.check()

    def check(
        self,
        registry: DatasetRegistry,
        selections: tuple[Selection, ...],
        *,
        purpose: str,
        expected_examples: tuple[Any, ...],
    ) -> None:
        rows = []
        matched = []
        for selection in sorted(selections, key=lambda s: s.item_id):
            item = registry._bound_item(selection.item_id, selection.source)
            expected_split = "heldout" if self.purpose == "evaluation" else "train"
            if item.descriptor["split"] != expected_split:
                raise ValueError("materialized use crosses split")
            authorize(self.batch.approve, item, purpose, self.batch.permission_ref)
            receipt = registry.db.execute(
                "SELECT receipt FROM items WHERE item_id=?", (selection.item_id,)
            ).fetchone()
            if receipt is None:
                raise ValueError("materialized receipt missing")
            rows.append(
                {
                    "item": item.descriptor,
                    "snapshot_sha256": selection.source.snapshot,
                    "receipt_sha256": c.digest(receipt[0]),
                }
            )
            # Match the exact original input/output/family, not an arbitrary
            # caller-selected subset or a fresh output inferred from a score.
            lineage = selection.source.store.get("lineage", selection.source.snapshot)
            collected = selection.source.store.get(
                "feedback_collection", lineage["collection_evidence"]
            )
            feedback = [
                f
                for row, f in zip(collected["selected"], lineage["batch"][2])
                if row["selection"] == vars(selection.source.selection)
            ]
            if len(feedback) != 1:
                raise ValueError("materialized exact attribution changed")
            matches = [
                e
                for e in expected_examples
                if feedback[0] in e.feedback
                and c.canonical(e.source.content.decode("utf-8")) == item.input_bytes
                and e.gold == item.output_bytes
                and e.output.content == item.output_bytes
                and c.digest(
                    c.canonical({"refinery_source_family": e.source.family_id})
                )
                == item.descriptor["family_sha256"]
            ]
            if len(matches) != 1:
                raise ValueError("materialized item not exact phase example")
            matched.append(matches[0])
        if len(matched) != len(expected_examples) or set(matched) != set(
            expected_examples
        ):
            raise ValueError("materialized batch does not cover exact phase")
        actual = c.canonical(
            {
                "version": "refinery.dataset-batch.v1",
                "purpose": self.purpose,
                "items": rows,
            }
        )
        if actual != self.manifest:
            raise ValueError("materialized batch binding changed")

    def fence(
        self, registry: DatasetRegistry, selections: tuple[Selection, ...]
    ) -> None:
        # Session's existing use_guard enforces its own enrolled/heldout owner.
        # This helper supplies only independent callback-free negative fences.
        for selection in selections:
            source_fence(selection)
            registry.available(selection.item_id)

    def seal(self) -> tuple[Any, ...]:
        return (
            self.manifest,
            self.identity,
            self.purpose,
            self.batch.manifest,
            id(self.batch.registry),
            id(self.batch.approve),
            self.batch.permission_ref,
            tuple(
                (s.item_id, id(s.source), s.source.snapshot, id(s.source.use))
                for s in self.batch.selections
            ),
            self.registry.seal(),
            self.feedback.seal(),
            None if self.source is None else self.source.seal(),
            id(self.use),
            id(self.use.verify_material),
            id(self.use.approve_use),
            id(self.use.dependency),
            id(self.use.tether_store),
            id(self.use.source_guard),
        )
