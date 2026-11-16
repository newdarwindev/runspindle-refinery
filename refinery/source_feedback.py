"""Opt-in deny-only task-source fences, never a source of positive permission."""

import json
from dataclasses import dataclass
from typing import Any, Callable

from .tether_dependency import TetherDependency


def require_no_guarded_identity(store: Any, keys: set[tuple[str, str]]) -> None:
    """Do not discard a known source fence binding by recollecting as legacy v1."""
    for (identity,) in store.db.execute(
        "SELECT identity FROM objects WHERE kind='feedback_collection'"
    ):
        receipt = store.get("feedback_collection", identity)
        if receipt["version"] == "retained_feedback_collection.v2" and any(
            (row["selection"]["namespace"], row["selection"]["feedback_id"]) in keys
            for row in receipt["selected"]
        ):
            raise ValueError("known source-bound feedback requires source guard")


@dataclass(frozen=True)
class SourceFeedbackGuard:
    """Bind the trusted material mapping to one actual pinned observation store.

    The resolver is a host/source authority, not caller-supplied provider text.
    Existing material and consent checks remain mandatory. Unknown only means
    this particular negative ledger contains no fence; it is not freshness proof.
    """

    dependency: TetherDependency
    observation_store: Any
    resolve_source: Callable[[dict[str, Any], Any], Any]

    def observe(self, target: dict[str, Any], material: Any) -> dict[str, Any]:
        module = self.dependency.modules["tether.source_observation"]
        if type(self.observation_store) is not module.SourceObservationStore:
            raise ValueError("unverified source observation store")
        original = self.resolve_source(dict(target), material)
        if type(original) is not module.RetainedSource:
            raise ValueError("source binding unavailable")
        binding = json.loads(original.encoded())
        retained = self.observation_store.resolve_original(original.source)
        if (
            type(retained) is not module.RetainedSource
            or retained.encoded() != original.encoded()
            or original.task_id != target["task_id"]
            or original.source.workspace_id != target["workspace_id"]
            or original.source.channel_id != target["channel_id"]
            or original.original_thread_ts != target["thread_id"]
            or target["task_revision"] != 1
        ):
            raise ValueError("source historical target mismatch")
        state = self.observation_store.state(original.source)
        if state not in ("unknown", "fenced"):
            raise ValueError("source observation unavailable")
        return {"original": binding, "state": state}

    def reread(self, observation: dict[str, Any]) -> dict[str, Any]:
        """Callback-free final negative-state read after all authority callbacks."""
        module = self.dependency.modules["tether.source_observation"]
        if type(self.observation_store) is not module.SourceObservationStore:
            raise ValueError("unverified source observation store")
        key = module.SourceKey(**observation["original"]["source"])
        state = self.observation_store.state(key)
        if state not in ("unknown", "fenced"):
            raise ValueError("source observation unavailable")
        return {"original": observation["original"], "state": state}
