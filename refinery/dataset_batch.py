"""Immutable local batch identity and fresh guarded reads, not execution authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from . import dataset_contract as c
from .dataset_materialization import Authority, DatasetRegistry
from .feedback_projection import RetainedSource


@dataclass(frozen=True)
class Selection:
    item_id: str
    source: RetainedSource


def source_fence(selection: Selection) -> None:
    """Callback-free retained feedback/source fence, never positive permission."""
    source = selection.source
    value = source.store.get("lineage", source.snapshot)
    receipt = source.store.get("feedback_collection", value["collection_evidence"])
    canonical = source.use.dependency.modules["tether.admission"].canonical
    for row in receipt["selected"]:
        selected = row["selection"]
        evidence = source.use.tether_store.evidence(
            selected["namespace"], selected["feedback_id"]
        )
        if c.digest(canonical(evidence)) != row["evidence_sha256"]:
            raise ValueError("batch feedback changed")
        if receipt["version"] == "retained_feedback_collection.v2":
            guard = source.use.source_guard
            observed = row["source_observation"]
            if guard is None or guard.reread(observed) != observed:
                raise ValueError("batch source changed")


def final_fence(registry: DatasetRegistry, selection: Selection, purpose: str) -> None:
    """No callbacks after the last use-authority callback in the batch."""
    source_fence(selection)
    source = selection.source
    value = source.store.get("lineage", source.snapshot)
    # The exact configuration is irrelevant with owner=None: enrolled or sealed
    # cohorts always deny. Never borrow an optimizer's policy owner here.
    from .heldout_policy import check

    check(
        source.store,
        value["dataset"],
        value["split"],
        purpose=purpose,
        configuration_sha256=selection.item_id,
        owner=None,
    )
    registry.available(selection.item_id)


def capture(
    registry: DatasetRegistry,
    selections: tuple[Selection, ...],
    *,
    purpose: str,
    approve: Authority,
    permission_ref: str,
) -> tuple[bytes, tuple[tuple[Any, bytes], ...]]:
    """Read a bounded batch; manifest contains hashes only, never a stored grant.

    Call again and compare the manifest at every eventual data-use boundary.
    This does not run DSPy, enroll cohorts or make cross-store reads atomic.
    """
    if (
        type(registry) is not DatasetRegistry
        or type(selections) is not tuple
        or not 1 <= len(selections) <= 100
    ):
        raise ValueError("bounded exact local batch required")
    if purpose not in {"optimization", "evaluation"}:
        raise ValueError("explicit data-use purpose required")
    if any(
        type(s) is not Selection
        or type(s.source) is not RetainedSource
        or type(s.item_id) is not str
        or re.fullmatch(r"[0-9a-f]{64}", s.item_id) is None
        for s in selections
    ):
        raise ValueError("exact retained selections required")
    if len({s.item_id for s in selections}) != len(selections):
        raise ValueError("duplicate batch selection")
    ordered = sorted(selections, key=lambda s: s.item_id)
    rows, contents = [], []
    datasets, occurrences, inputs = set(), set(), set()
    for selection in ordered:
        content = registry.read(
            selection.item_id,
            selection.source,
            purpose=purpose,
            approve=approve,
            permission_ref=permission_ref,
        )
        row = registry.db.execute(
            "SELECT descriptor,receipt FROM items WHERE item_id=?", (selection.item_id,)
        ).fetchone()
        if row is None:
            raise ValueError("batch item disappeared")
        descriptor = json.loads(row[0])
        if (
            c.canonical(descriptor) != row[0]
            or descriptor["item_id"] != selection.item_id
        ):
            raise ValueError("batch descriptor changed")
        if (
            c.binding(
                descriptor["dataset"],
                descriptor["source_identity_sha256"],
                content[0],
                content[1],
                descriptor["family_sha256"],
                descriptor["split"],
            )
            != descriptor
        ):
            raise ValueError("batch content changed")
        if (
            descriptor["source_identity_sha256"] in occurrences
            or descriptor["input_sha256"] in inputs
        ):
            raise ValueError("duplicate batch occurrence or input")
        occurrences.add(descriptor["source_identity_sha256"])
        inputs.add(descriptor["input_sha256"])
        datasets.add(descriptor["dataset"])
        rows.append(
            {
                "item": descriptor,
                "snapshot_sha256": selection.source.snapshot,
                "receipt_sha256": c.digest(row[1]),
            }
        )
        contents.append(content)
    if len(datasets) != 1:
        raise ValueError("batch crosses datasets")
    for selection in ordered:
        final_fence(registry, selection, purpose)
    manifest = c.canonical(
        {"version": "refinery.dataset-batch.v1", "purpose": purpose, "items": rows}
    )
    return manifest, tuple(contents)


def read(
    expected_manifest: bytes,
    registry: DatasetRegistry,
    selections: tuple[Selection, ...],
    *,
    purpose: str,
    approve: Authority,
    permission_ref: str,
) -> tuple[tuple[Any, bytes], ...]:
    if (
        type(expected_manifest) is not bytes
        or not 1 <= len(expected_manifest) <= 262144
    ):
        raise ValueError("bounded immutable batch manifest required")
    manifest, contents = capture(
        registry,
        selections,
        purpose=purpose,
        approve=approve,
        permission_ref=permission_ref,
    )
    if manifest != expected_manifest:
        raise ValueError("batch snapshot changed")
    return contents
