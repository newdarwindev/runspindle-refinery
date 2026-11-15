"""Store-scoped family enrollment and one-shot held-out seals.

No schema, network authority, promotion, or cross-store global guarantee. The
existing immutable object table and transaction boundary own these policy rows.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from . import evaluation as ev
from .lineage import Dataset, LockedSplit, digest
from .optimization import validate_dataset
from .storage import Store, unpack


def partitions(dataset: Dataset, split: LockedSplit) -> dict[str, str]:
    validate_dataset(dataset, split)
    return {
        e.source.family_id: split.partition(e.source.family_id)
        for e in dataset.examples
    }


def retained(store: Store, kind: str, family: str) -> Any:
    db: Any = store
    key = digest(family.encode())
    row = db.db.execute(
        "SELECT digest,payload FROM objects WHERE kind=? AND identity=?", (kind, key)
    ).fetchone()
    if row is None:
        return None
    if digest(row[1]) != row[0]:
        raise ValueError("corrupt heldout policy")
    decode: Any = unpack
    return decode(row[1])


def check(
    store: Store,
    dataset: Dataset,
    split: LockedSplit,
    *,
    purpose: str,
    configuration_sha256: str,
    owner: str | None,
) -> None:
    """Read the latest policy inside the caller's claim/use transaction, if any."""
    db: Any = store
    with nullcontext() if db.db.in_transaction else db.transaction():
        roles = partitions(dataset, split)
        for family, partition in roles.items():
            prior = retained(store, "dspy_partition", family)
            if prior is not None:
                if prior != {
                    "version": "dspy.partition.v1",
                    "family": family,
                    "partition": partition,
                }:
                    raise ev.Halt("locked_family_partition")
                if owner is None:
                    raise ev.Halt("enrolled_dspy_cohort")
            seal = retained(store, "dspy_holdout", family)
            if seal is not None and prior is None:
                raise ev.Halt("unenrolled_dspy_cohort")
            if seal is not None and not (
                type(seal) is dict
                and seal.get("version") == "dspy.heldout.v1"
                and seal.get("owner") == owner
                and purpose == "evaluation"
                and seal.get("dataset_sha256") == split.dataset_sha256
                and seal.get("split_sha256") == split.sha256
                and seal.get("configuration_sha256") == configuration_sha256
                and dict(seal.get("partitions", ())) == roles
            ):
                raise ev.Halt("sealed_heldout_cohort")


def enroll(
    store: Store,
    tx: Any,
    dataset: Dataset,
    split: LockedSplit,
    *,
    owner: str,
    configuration_sha256: str,
) -> None:
    """Enroll before a fresh compiler claim, rejecting ambiguous legacy exposure."""
    check(
        store,
        dataset,
        split,
        purpose="optimization",
        configuration_sha256=configuration_sha256,
        owner=owner,
    )
    roles = partitions(dataset, split)
    family_by_output = {
        identity.removeprefix("output:"): family
        for identity, family in tx.execute("SELECT identity,family FROM families")
        if identity.startswith("output:")
    }
    decode: Any = unpack
    for (raw,) in tx.execute("SELECT request FROM attempts"):
        request = decode(raw)
        if type(request) is not ev.Request or request.output_id not in family_by_output:
            raise ev.Halt("ambiguous_prior_attempt")
        family = family_by_output[request.output_id]
        if family not in roles:
            continue
        if request.stage not in {"optimization", "reflection"}:
            raise ev.Halt("prior_heldout_exposure")
        if roles[family] != "train":
            raise ev.Halt("prior_training_exposure")
    db: Any = store
    for family, partition in roles.items():
        db.put(
            tx,
            "dspy_partition",
            digest(family.encode()),
            {"version": "dspy.partition.v1", "family": family, "partition": partition},
        )


def seal(
    store: Store,
    tx: Any,
    dataset: Dataset,
    split: LockedSplit,
    *,
    owner: str,
    configuration_sha256: str,
    candidate_sha256: str,
) -> None:
    check(
        store,
        dataset,
        split,
        purpose="evaluation",
        configuration_sha256=configuration_sha256,
        owner=owner,
    )
    roles = partitions(dataset, split)
    value = {
        "version": "dspy.heldout.v1",
        "owner": owner,
        "dataset_sha256": split.dataset_sha256,
        "split_sha256": split.sha256,
        "configuration_sha256": configuration_sha256,
        "candidate_sha256": candidate_sha256,
        "partitions": tuple(sorted(roles.items())),
    }
    db: Any = store
    for family in roles:
        prior = retained(store, "dspy_partition", family)
        if prior is None:
            raise ev.Halt("unenrolled_dspy_cohort")
        db.put(tx, "dspy_holdout", digest(family.encode()), value)


def verify_seal(
    store: Store,
    dataset: Dataset,
    split: LockedSplit,
    *,
    owner: str,
    configuration_sha256: str,
    candidate_sha256: str,
) -> None:
    roles = partitions(dataset, split)
    expected = {
        "version": "dspy.heldout.v1",
        "owner": owner,
        "dataset_sha256": split.dataset_sha256,
        "split_sha256": split.sha256,
        "configuration_sha256": configuration_sha256,
        "candidate_sha256": candidate_sha256,
        "partitions": tuple(sorted(roles.items())),
    }
    for family in roles:
        if retained(store, "dspy_holdout", family) != expected:
            raise ev.Halt("heldout_seal_changed")
