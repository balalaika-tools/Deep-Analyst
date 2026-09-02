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
    OutcomeRejectedError,
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
    return InvestigationState(control=ControlState(case_id="case-1", policy_version="policy-1"))


def _item(evidence_id: str, *, case_id: str = "case-1", status: str = "verified") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind="row",
        case_id=case_id,
        content_hash=canonical_fingerprint({"id": evidence_id}),
        source_refs=(
            SourceRef(record_id=f"record-{evidence_id}", locator=FieldLocator(field="x")),
        ),
        content=f"evidence {evidence_id}",
        evidentiary_status=status,
    )


def _outcome(call_id: str, *items: EvidenceItem, case_id: str = "case-1") -> ToolOutcome:
    return ToolOutcome(
        call_id=call_id,
        intent_fingerprint=canonical_fingerprint({"intent": call_id}),
        tool="query_records",
        case_id=case_id,
        status=OutcomeStatus.QUERY_SUFFICIENT,
        evidence=items,
        consumption=BudgetConsumption(tool_calls=1, physical_attempts=1, rows=len(items)),
    )


def test_control_is_frozen_and_rejects_model_authored_scope_fields() -> None:
    state = _state()
    with pytest.raises(ValidationError):
        state.control.case_id = "case-2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ControlState.model_validate(
            {"case_id": "case-1", "policy_version": "v1", "model_case_id": "case-2"}
        )
    with pytest.raises(ValidationError):
        InvestigationState.model_validate({"control": state.control, "ledger": {}})


def test_state_round_trips_as_plain_json_sections_and_rejects_other_versions() -> None:
    turn = new_turn_state(
        turn_id="turn-1",
        request_id="request-1",
        message_id="message-1",
        utterance="Trace the transfer",
        case_id="case-1",
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
    assert STATE_SCHEMA_VERSION == 2


def test_upsert_is_idempotent_and_never_promotes_status_or_overwrites_provenance() -> None:
    first = _outcome("call-1", _item("ev-1", status="proposed"))
    index = upsert_evidence(
        EvidenceIndex(), first, case_id="case-1", turn_id="turn-1", max_cards=10
    )
    replay = upsert_evidence(index, first, case_id="case-1", turn_id="turn-1", max_cards=10)
    assert replay == index

    promoted = _outcome("call-2", _item("ev-1", status="confirmed"))
    later = upsert_evidence(index, promoted, case_id="case-1", turn_id="turn-2", max_cards=10)

    assert later.cards["ev-1"].evidentiary_status == "proposed"
    assert later.cards["ev-1"].first_seen_turn_id == "turn-1"
    assert later.cards["ev-1"].source_refs == index.cards["ev-1"].source_refs
    assert later.next_sequence == 2


def test_upsert_rejects_cross_case_evidence_before_changing_the_index() -> None:
    with pytest.raises(OutcomeRejectedError):
        upsert_evidence(
            EvidenceIndex(),
            _outcome("call-1", _item("foreign", case_id="case-2"), case_id="case-2"),
            case_id="case-1",
            turn_id="turn-1",
            max_cards=10,
        )
    with pytest.raises(OutcomeRejectedError):
        upsert_evidence(
            EvidenceIndex(),
            _outcome("call-1", _item("foreign", case_id="case-2")),
            case_id="case-1",
            turn_id="turn-1",
            max_cards=10,
        )


def test_bound_drops_oldest_unreferenced_cards_and_records_a_coverage_notice() -> None:
    index = EvidenceIndex()
    for turn, evidence_id in (("turn-1", "a"), ("turn-1", "b"), ("turn-2", "c")):
        index = upsert_evidence(
            index,
            _outcome(evidence_id, _item(evidence_id)),
            case_id="case-1",
            turn_id=turn,
            max_cards=10,
        )

    bounded = upsert_evidence(
        index,
        _outcome(
            "call-d",
            _item("d"),
        ),
        case_id="case-1",
        turn_id="turn-3",
        max_cards=3,
        protected_ids=("a",),
    )

    assert sorted(bounded.cards) == ["a", "c", "d"]
    assert bounded.dropped_cards == 1
    assert bounded.coverage_notice == EVIDENCE_INDEX_BOUNDED_NOTICE
    assert bounded.next_sequence == 5
