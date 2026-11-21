"""Evidence projection from the durable GEPA evaluator's verified heldout report."""

from __future__ import annotations

from typing import Any

from .evaluation import Attempt
from .lineage import Example, digest
from .storage import pack


def case_digest(output_id: str, revision: str) -> str:
    return digest(pack((output_id, revision)))


def case_roster(examples: tuple[Example, ...]) -> list[str]:
    return sorted(
        case_digest(example.output.output_id, example.output.revision)
        for example in examples
    )


def paired_rows(
    heldout: tuple[Example, ...],
    attempts: tuple[Attempt, ...],
    predictions: tuple[tuple[str, str, str, str], ...],
) -> dict[str, list[dict[str, Any]]]:
    gold = {
        (example.output.output_id, example.output.revision): digest(example.gold)
        for example in heldout
    }
    totals: dict[tuple[str, str, str], tuple[int, int]] = {}
    for attempt in attempts:
        request = attempt.request
        key = request.stage, request.output_id, request.output_revision
        cost, tokens = attempt.cost_microunits, attempt.tokens
        if type(cost) is not int or type(tokens) is not int or min(cost, tokens) < 0:
            raise ValueError("unresolved GEPA measurement usage")
        previous_cost, previous_tokens = totals.get(key, (0, 0))
        totals[key] = previous_cost + cost, previous_tokens + tokens

    rows: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    for stage, output, revision, answer in predictions:
        cost, tokens = totals[stage, output, revision]
        rows[stage].append(
            {
                "case": case_digest(output, revision),
                "correct": answer == gold[output, revision],
                "cost_micros": cost,
                "tokens": tokens,
            }
        )
    return {
        stage: sorted(values, key=lambda row: row["case"])
        for stage, values in rows.items()
    }
