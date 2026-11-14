"""Persist bounded scripted selection; interrupted ownership never auto-replays.

Uses the existing component store and cumulative budget, without a provider,
serving, authentication, deployment or global held-out-policy claim.
"""

from __future__ import annotations

from typing import Any, Callable, cast

from . import evaluation as ev
from .durable_evaluation import SQLiteBudget
from .lineage import _token, digest
from .optimization import Selection, _prepare, optimize
from .storage import Store, VERSION, pack, version

encode = cast(Callable[[Any], bytes], pack)
check_version = cast(Callable[[str], None], version)


def run(
    store: Store,
    api_version: str,
    identity: str,
    snapshot_sha256: str,
    candidates: tuple[bytes, ...],
    *,
    provider: ev.ScriptedProvider,
    limits: ev.Limits,
    per_call: ev.Reservation,
    budget_id: str,
    max_attempts: int = 1,
    checkpoint: Callable[[str], None] = lambda _: None,
) -> Selection:
    """Claim once, cache committed results, reject uncertain/interrupted repeats.

    Clock must retain its epoch across processes, as required by SQLiteBudget.
    Result recovery does not resume the scripted provider cursor. A different
    identity is a new run, still bounded by the same explicitly supplied ledger.
    """
    check_version(api_version)
    _token(identity)
    _token(budget_id)
    db: Any = store
    scripted: Any = provider
    dataset, split = db.lineage(snapshot_sha256)
    # Pure validation before any claim/budget writes, also on cached reads.
    probe = ev.Budget(limits, clock_ms=scripted.clock)
    train, inventory, script_sha, config = _prepare(
        dataset, split, candidates, provider, probe, per_call, max_attempts
    )
    # Digest namespace keeps arbitrary caller identity bounded. Evaluator records
    # with this same key cannot match our domain-separated immutable binding.
    key = "selection-" + digest(identity.encode())
    binding = encode((VERSION, "selection.v1", snapshot_sha256, config, budget_id))
    with db.transaction() as tx:
        row = tx.execute(
            "SELECT binding,state,report FROM experiments WHERE identity=?", (key,)
        ).fetchone()
        if row:
            if row[0] != binding:
                raise ValueError("immutable selection conflict")
            if row[1] == "complete":
                result = db.get("selection", row[2])
                if (
                    type(result) is not Selection
                    or result.dataset_sha256 != split.dataset_sha256
                    or result.split_sha256 != split.sha256
                    or result.configuration_sha256 != config
                    or result.provider_script_sha256 != script_sha
                    or result.evidence_class != "scripted_training_selection"
                    or result.training_cases
                    != tuple((e.output.output_id, e.output.revision) for e in train)
                ):
                    raise ValueError("invalid selection result association")
                if result.status == "selected":
                    if (
                        type(result.selected) is not bytes
                        or result.selected not in candidates
                        or result.selected_sha256 != digest(result.selected)
                        or result.reason is not None
                        or type(result.candidate_scores) is not tuple
                        or len(result.candidate_scores) != len(inventory)
                        or any(
                            type(pair) is not tuple
                            or len(pair) != 2
                            or type(pair[0]) is not str
                            or type(pair[1]) is not int
                            or not 0 <= pair[1] <= len(train)
                            for pair in result.candidate_scores
                        )
                    ):
                        raise ValueError("invalid selected candidate")
                    scores = dict(result.candidate_scores)
                    if set(scores) != set(inventory) or result.selected_sha256 != min(
                        scores, key=lambda sha: (-scores[sha], sha)
                    ):
                        raise ValueError("invalid selection scores")
                elif (
                    result.status != "blocked"
                    or type(result.reason) is not str
                    or not result.reason
                    or result.selected is not None
                    or result.selected_sha256 is not None
                ):
                    raise ValueError("invalid selection status")
                return result
            raise ev.Halt("interrupted_or_owned_selection")
        if scripted.calls:
            raise ValueError("new selection requires pristine provider cursor")
        db.room(tx, "experiments")
        tx.execute("INSERT INTO experiments VALUES(?,?,'running',NULL)", (key, binding))
    checkpoint("after_selection_claim")
    ledger = SQLiteBudget(
        store,
        budget_id,
        limits,
        clock_ms=scripted.clock,
        experiment=key,
        checkpoint=checkpoint,
    )
    result = optimize(
        dataset,
        split,
        candidates,
        provider=provider,
        budget=ledger,
        per_call=per_call,
        max_attempts=max_attempts,
    )
    with db.transaction() as tx:
        sha = digest(encode(result))
        db.put(tx, "selection", sha, result)
        tx.execute(
            "UPDATE experiments SET state='complete',report=? "
            "WHERE identity=? AND state='running'",
            (sha, key),
        )
        checkpoint("before_selection_commit")
    checkpoint("after_selection_commit")
    return result
