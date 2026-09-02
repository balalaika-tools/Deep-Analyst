from __future__ import annotations

import asyncio

import pytest
from evidence_model import FieldLocator, SourceRef
from investigation_agent.domain.investigation_state import (
    ControlState,
    EvidenceCard,
    EvidenceIndex,
    InvestigationState,
    ProjectedFinding,
    ReferentBinding,
    WorkingProjection,
)
from investigation_agent.domain.tool_outcome import canonical_fingerprint
from investigation_agent.genai.shared.retries import CancellationToken, RetryPolicy
from investigation_agent.genai.state_projection.compactor import (
    ProjectionValidationError,
    run_projection,
    stale_projection,
    validate_projection,
)
from investigation_agent.genai.state_projection.schemas import (
    ProjectionInput,
    build_projection_input,
)


def _card(evidence_id: str, *, kind: str = "row", status: str = "verified") -> EvidenceCard:
    return EvidenceCard(
        evidence_id=evidence_id,
        kind=kind,
        case_id="case-1",
        content_hash=canonical_fingerprint(evidence_id),
        source_refs=(SourceRef(record_id="record-1", locator=FieldLocator(field="amount")),),
        evidentiary_status=status,
        tool="query_records",
        display="amount 50",
        first_seen_turn_id="turn-2",
        sequence=1,
    )


def _state(*cards: EvidenceCard) -> InvestigationState:
    return InvestigationState(
        control=ControlState(case_id="case-1", policy_version="v1"),
        evidence=EvidenceIndex(cards={card.evidence_id: card for card in cards}),
    )


def _request(
    state: InvestigationState,
    *,
    predecessor: WorkingProjection | None = None,
    incomplete: bool = False,
) -> ProjectionInput:
    return build_projection_input(
        source_turn_id="turn-2",
        utterance="What did the transfer fund?",
        predecessor=predecessor or WorkingProjection(source_turn_id="turn-1", user_goal="trace"),
        evidence_added=tuple(state.evidence.cards.values()),
        outcome="completed",
        answer="The transfer funded account 77.",
        coverage_incomplete=incomplete,
    )


def _candidate(
    *, evidence_id: str = "evidence-1", statement: str = "A transfer of 50 is recorded."
) -> WorkingProjection:
    return WorkingProjection(
        source_turn_id="turn-2",
        user_goal="trace the transfer",
        focus_evidence_ids=(evidence_id,),
        active_findings=(
            ProjectedFinding(
                finding_id="finding-1", statement=statement, evidence_ids=(evidence_id,)
            ),
        ),
    )


def test_projection_accepts_only_indexed_identifiers_bound_to_this_turn() -> None:
    state = _state(_card("evidence-1"))
    request = _request(state)

    assert validate_projection(_candidate(), request, state).source_turn_id == "turn-2"

    for changed, violation in (
        (_candidate().model_copy(update={"source_turn_id": "turn-1"}), "source_turn_id"),
        (_candidate(evidence_id="invented"), "unknown_evidence_ids"),
        (_candidate().model_copy(update={"projection_stale": True}), "projection_stale"),
    ):
        with pytest.raises(ProjectionValidationError, match=violation):
            validate_projection(changed, request, state)


def test_projection_rejects_unqualified_proposed_findings_and_absence_from_misses() -> None:
    state = _state(_card("evidence-1", status="proposed"))
    with pytest.raises(ProjectionValidationError, match="unqualified_proposed_finding"):
        validate_projection(_candidate(), _request(state), state)
    assert validate_projection(
        _candidate(statement="Proposed: a transfer of 50 may be recorded."), _request(state), state
    )

    incomplete = _request(_state(_card("evidence-1")), incomplete=True)
    with pytest.raises(ProjectionValidationError, match="absence_claim"):
        validate_projection(
            _candidate(statement="The refund does not exist."),
            incomplete,
            _state(_card("evidence-1")),
        )


def test_referent_bindings_may_target_entities_or_evidence_only() -> None:
    state = _state(_card("evidence-1"), _card("entity:acct-77", kind="entity"))
    bound = _candidate().model_copy(
        update={
            "referent_bindings": (
                ReferentBinding(
                    phrase="the account", target_id="entity:acct-77", confidence="resolved"
                ),
                ReferentBinding(phrase="the refund", target_id=None, confidence="unresolved"),
            )
        }
    )
    assert validate_projection(bound, _request(state), state) is bound

    unknown = bound.model_copy(
        update={
            "referent_bindings": (
                ReferentBinding(
                    phrase="the courier", target_id="entity:missing", confidence="ambiguous"
                ),
            )
        }
    )
    with pytest.raises(ProjectionValidationError, match="unknown_referent_targets"):
        validate_projection(unknown, _request(state), state)


def test_stale_close_preserves_prior_projection() -> None:
    request = _request(_state(_card("evidence-1")))

    stale = stale_projection(request)

    assert stale.user_goal == request.predecessor.user_goal
    assert stale.source_turn_id == "turn-1"
    assert stale.projection_stale is True


@pytest.mark.asyncio
async def test_one_repair_is_allowed_and_a_failed_replacement_keeps_the_prior_projection() -> None:
    state = _state(_card("evidence-1"))
    request = _request(state)
    calls: list[tuple[str, ...]] = []

    async def repairing_model(
        req: ProjectionInput, *, repair_violations: tuple[str, ...] = ()
    ) -> WorkingProjection:
        calls.append(repair_violations)
        return _candidate(evidence_id="invented") if not repair_violations else _candidate()

    async def hopeless_model(
        req: ProjectionInput, *, repair_violations: tuple[str, ...] = ()
    ) -> WorkingProjection:
        return _candidate(evidence_id="invented")

    policy = RetryPolicy(
        max_attempts=1, initial_delay_s=0, backoff_factor=1, max_delay_s=0, jitter=False
    )
    deadline = asyncio.get_running_loop().time() + 5
    repaired = await run_projection(
        request,
        state,
        model=repairing_model,
        retry_policy=policy,
        transient_errors=(TimeoutError,),
        cancellation=CancellationToken.create(),
        deadline=deadline,
    )
    assert repaired.stale is False
    assert repaired.model_calls == 2
    assert calls[1] and "unknown_evidence_ids" in calls[1][0]

    stale = await run_projection(
        request,
        state,
        model=hopeless_model,
        retry_policy=policy,
        transient_errors=(TimeoutError,),
        cancellation=CancellationToken.create(),
        deadline=deadline,
    )
    assert stale.stale is True
    assert stale.projection == request.predecessor.model_copy(update={"projection_stale": True})


@pytest.mark.asyncio
async def test_no_model_call_starts_after_cancellation_or_without_closure_reserve() -> None:
    state = _state(_card("evidence-1"))
    request = _request(state)
    started = 0

    async def model(
        req: ProjectionInput, *, repair_violations: tuple[str, ...] = ()
    ) -> WorkingProjection:
        nonlocal started
        started += 1
        return _candidate()

    policy = RetryPolicy(
        max_attempts=1, initial_delay_s=0, backoff_factor=1, max_delay_s=0, jitter=False
    )
    cancelled = CancellationToken.create()
    cancelled.cancel()
    deadline = asyncio.get_running_loop().time() + 5
    result = await run_projection(
        request,
        state,
        model=model,
        retry_policy=policy,
        transient_errors=(),
        cancellation=cancelled,
        deadline=deadline,
    )
    assert result.stale and started == 0

    result = await run_projection(
        request,
        state,
        model=model,
        retry_policy=policy,
        transient_errors=(),
        cancellation=CancellationToken.create(),
        deadline=deadline,
        can_start_model=lambda: False,
    )
    assert result.stale and started == 0
