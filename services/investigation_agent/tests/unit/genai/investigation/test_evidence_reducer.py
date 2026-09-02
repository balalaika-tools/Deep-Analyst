"""The ``evidence`` channel reducer must honour the bound ``upsert_evidence`` enforced."""

from __future__ import annotations

from typing import Any

from evidence_model import FieldLocator, SourceRef
from investigation_agent.domain.investigation_state import EvidenceIndex, upsert_evidence
from investigation_agent.domain.tool_outcome import (
    BudgetConsumption,
    EvidenceItem,
    OutcomeStatus,
    ToolOutcome,
    canonical_fingerprint,
)
from investigation_agent.genai.investigation.schemas import merge_evidence_indexes


def _outcome(*ids: str) -> ToolOutcome:
    items = tuple(
        EvidenceItem(
            evidence_id=evidence_id,
            kind="row",
            content_hash=canonical_fingerprint(evidence_id),
            source_refs=(
                SourceRef(record_id=f"record-{evidence_id}", locator=FieldLocator(field="x")),
            ),
            content=f"evidence {evidence_id}",
            evidentiary_status="verified",
        )
        for evidence_id in ids
    )
    return ToolOutcome(
        call_id="call",
        intent_fingerprint=canonical_fingerprint(ids),
        tool="query_records",
        status=OutcomeStatus.QUERY_SUFFICIENT,
        evidence=items,
        consumption=BudgetConsumption(tool_calls=1, physical_attempts=1, rows=len(items)),
    )


def _apply(channel: dict[str, Any], *ids: str, turn_id: str, max_cards: int) -> dict[str, Any]:
    """One tool call: upsert against the channel snapshot, then reduce back into it."""

    updated = upsert_evidence(
        EvidenceIndex.model_validate(channel),
        _outcome(*ids),
        turn_id=turn_id,
        max_cards=max_cards,
    )
    return merge_evidence_indexes(channel, updated.model_dump(mode="json"))


def test_sequential_tool_calls_keep_the_bound_and_count_every_drop() -> None:
    channel = merge_evidence_indexes(None, EvidenceIndex().model_dump(mode="json"))
    channel = _apply(channel, "a", "b", turn_id="turn-1", max_cards=2)

    channel = _apply(channel, "c", turn_id="turn-2", max_cards=2)
    channel = _apply(channel, "d", turn_id="turn-3", max_cards=2)
    channel = _apply(channel, "e", turn_id="turn-4", max_cards=2)

    index = EvidenceIndex.model_validate(channel)
    assert sorted(index.cards) == ["d", "e"]
    assert index.dropped_cards == 3
    assert index.coverage_notice is not None


def test_parallel_tool_calls_of_one_step_keep_each_other_but_not_evicted_cards() -> None:
    channel = merge_evidence_indexes(None, EvidenceIndex().model_dump(mode="json"))
    snapshot = _apply(channel, "a", "b", turn_id="turn-1", max_cards=2)
    first = upsert_evidence(
        EvidenceIndex.model_validate(snapshot),
        _outcome("c"),
        turn_id="turn-2",
        max_cards=2,
    )
    second = upsert_evidence(
        EvidenceIndex.model_validate(snapshot),
        _outcome("d"),
        turn_id="turn-2",
        max_cards=2,
    )

    results = []
    for left, right in ((first, second), (second, first)):
        merged = merge_evidence_indexes(
            merge_evidence_indexes(snapshot, left.model_dump(mode="json")),
            right.model_dump(mode="json"),
        )
        results.append(EvidenceIndex.model_validate(merged))

    assert results[0] == results[1]
    assert sorted(results[0].cards) == ["c", "d"]
    assert results[0].dropped_cards == 2
    assert results[0].max_cards == 2
    assert results[0].cards["c"].sequence < results[0].cards["d"].sequence
