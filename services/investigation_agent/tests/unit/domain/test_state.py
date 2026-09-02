from __future__ import annotations

from datetime import UTC, datetime

import pytest
from evidence_model import FieldLocator, SourceRef
from investigation_agent.domain.investigation_state import (
    EVIDENCE_INDEX_BOUNDED_NOTICE,
    STATE_SCHEMA_VERSION,
    ControlState,
    EvidenceIndex,
    IncompatibleStateError,
    InvestigationState,
    WorkingProjection,
    new_turn_state,
    parse_state,
    state_update,
    upsert_evidence,
)
from investigation_agent.domain.tool_outcome import (
    BudgetConsumption,
    EvidenceItem,
    OutcomeStatus,
    ToolOutcome,
    canonical_fingerprint,
)
from pydantic import ValidationError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _state() -> InvestigationState:
    return InvestigationState(control=ControlState(policy_version="policy-1"))


def _item(evidence_id: str, *, status: str = "verified") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind="row",
        content_hash=canonical_fingerprint({"id": evidence_id}),
        source_refs=(
            SourceRef(record_id=f"record-{evidence_id}", locator=FieldLocator(field="x")),
        ),
        content=f"evidence {evidence_id}",
        evidentiary_status=status,
    )


def _outcome(call_id: str, *items: EvidenceItem) -> ToolOutcome:
    return ToolOutcome(
        call_id=call_id,
        intent_fingerprint=canonical_fingerprint({"intent": call_id}),
        tool="query_records",
        status=OutcomeStatus.QUERY_SUFFICIENT,
        evidence=items,
        consumption=BudgetConsumption(tool_calls=1, physical_attempts=1, rows=len(items)),
    )


def test_control_is_frozen_and_rejects_unknown_fields() -> None:
    state = _state()
    with pytest.raises(ValidationError):
        state.control.policy_version = "v2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ControlState.model_validate({"policy_version": "v1", "model_scope": "other"})
    with pytest.raises(ValidationError):
        InvestigationState.model_validate({"control": state.control, "ledger": {}})


def test_state_round_trips_as_plain_json_sections_and_rejects_other_versions() -> None:
    turn = new_turn_state(
        turn_id="turn-1",
        request_id="request-1",
        message_id="message-1",
        utterance="Trace the transfer",
        opened_at=NOW,
    )
    state = _state().model_copy(update={"turn": turn})

    update = state_update(control=state.control, turn=turn, projection=WorkingProjection())
    assert all(not hasattr(value, "model_dump") for value in update.values())
    parsed = parse_state({**state.as_update(), "messages": []})
    assert parsed == state
    assert parse_state({}) is None
    assert parse_state({"messages": []}) is None

    stale = {**state.as_update(), "control": {**update["control"], "state_schema_version": 1}}
    with pytest.raises(IncompatibleStateError):
        parse_state(stale)
    assert STATE_SCHEMA_VERSION == 3


def test_upsert_is_idempotent_and_never_promotes_status_or_overwrites_provenance() -> None:
    first = _outcome("call-1", _item("ev-1", status="proposed"))
    index = upsert_evidence(EvidenceIndex(), first, turn_id="turn-1", max_cards=10)
    replay = upsert_evidence(index, first, turn_id="turn-1", max_cards=10)
    assert replay == index

    promoted = _outcome("call-2", _item("ev-1", status="confirmed"))
    later = upsert_evidence(index, promoted, turn_id="turn-2", max_cards=10)

    assert later.cards["ev-1"].evidentiary_status == "proposed"
    assert later.cards["ev-1"].first_seen_turn_id == "turn-1"
    assert later.cards["ev-1"].source_refs == index.cards["ev-1"].source_refs
    assert later.next_sequence == 2


def test_bound_drops_oldest_unreferenced_cards_and_records_a_coverage_notice() -> None:
    index = EvidenceIndex()
    for turn, evidence_id in (("turn-1", "a"), ("turn-1", "b"), ("turn-2", "c")):
        index = upsert_evidence(
            index,
            _outcome(evidence_id, _item(evidence_id)),
            turn_id=turn,
            max_cards=10,
        )

    bounded = upsert_evidence(
        index,
        _outcome(
            "call-d",
            _item("d"),
        ),
        turn_id="turn-3",
        max_cards=3,
        protected_ids=("a",),
    )

    assert sorted(bounded.cards) == ["a", "c", "d"]
    assert bounded.dropped_cards == 1
    assert bounded.coverage_notice == EVIDENCE_INDEX_BOUNDED_NOTICE
    assert bounded.next_sequence == 5


def test_bound_remains_strict_when_one_turn_adds_more_cards_than_capacity() -> None:
    bounded = upsert_evidence(
        EvidenceIndex(),
        _outcome("call", *(_item(evidence_id) for evidence_id in ("a", "b", "c"))),
        turn_id="turn-1",
        max_cards=2,
    )

    assert sorted(bounded.cards) == ["b", "c"]
    assert bounded.dropped_cards == 1
    assert bounded.max_cards == 2
