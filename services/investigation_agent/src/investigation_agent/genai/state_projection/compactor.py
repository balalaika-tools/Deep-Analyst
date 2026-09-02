"""Projection validation, bounded retry/repair, and deterministic stale fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from investigation_agent.domain.investigation_state import InvestigationState, WorkingProjection
from investigation_agent.genai.shared.retries import (
    CancellationToken,
    OperationCancelledError,
    RetryPolicy,
    TransientExhaustedError,
    retry_async,
)
from investigation_agent.genai.state_projection.schemas import ProjectionInput


class ProjectionValidationError(ValueError):
    """A model-authored replacement does not match its deterministic input contract."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(violations))


type ProjectionModel = Callable[..., Awaitable[WorkingProjection]]


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    projection: WorkingProjection
    model_calls: int
    physical_attempts: int
    stale: bool


_ABSENCE_PHRASES = (
    "does not exist",
    "did not happen",
    "never occurred",
    "no evidence exists",
    "is absent",
)


def validate_projection(
    candidate: WorkingProjection, request: ProjectionInput, state: InvestigationState
) -> WorkingProjection:
    """Accept only a replacement bound to this turn that references indexed evidence."""

    violations: list[str] = []
    if candidate.source_turn_id != request.source_turn_id:
        violations.append("source_turn_id")
    if candidate.projection_stale:
        violations.append("projection_stale")
    allowed_ids = set(state.evidence.cards)
    entity_ids = {
        card.evidence_id for card in state.evidence.cards.values() if card.kind == "entity"
    }
    referenced = _projection_evidence_ids(candidate)
    if unknown := sorted(referenced - allowed_ids):
        violations.append(f"unknown_evidence_ids:{','.join(unknown)}")
    binding_targets = {item.target_id for item in candidate.referent_bindings if item.target_id}
    if unknown_bindings := sorted(binding_targets - allowed_ids - entity_ids):
        violations.append(f"unknown_referent_targets:{','.join(unknown_bindings)}")
    proposed_ids = {
        card.evidence_id
        for card in state.evidence.cards.values()
        if card.evidentiary_status == "proposed"
    }
    for finding in candidate.active_findings:
        if (
            proposed_ids.intersection(finding.evidence_ids)
            and "propos" not in finding.statement.lower()
        ):
            violations.append(f"unqualified_proposed_finding:{finding.finding_id}")
    if request.coverage_incomplete and _projection_claims_absence(candidate):
        violations.append("absence_claim_from_incomplete_coverage")
    if violations:
        raise ProjectionValidationError(tuple(violations))
    return candidate


def _projection_evidence_ids(candidate: WorkingProjection) -> set[str]:
    ids = set(candidate.focus_evidence_ids)
    ids.update(item for finding in candidate.active_findings for item in finding.evidence_ids)
    ids.update(item for hypothesis in candidate.hypotheses for item in hypothesis.evidence_ids)
    return ids


def _projection_claims_absence(candidate: WorkingProjection) -> bool:
    text = " ".join(
        (
            candidate.dialogue_summary,
            *(item.statement for item in candidate.active_findings),
            *(item.statement for item in candidate.hypotheses),
        )
    ).lower()
    return any(phrase in text for phrase in _ABSENCE_PHRASES)


def stale_projection(request: ProjectionInput) -> WorkingProjection:
    """Keep the last validated projection and mark it stale for the next turn."""

    return request.predecessor.model_copy(update={"projection_stale": True})


async def run_projection(
    request: ProjectionInput,
    state: InvestigationState,
    *,
    model: ProjectionModel,
    retry_policy: RetryPolicy,
    transient_errors: tuple[type[BaseException], ...],
    cancellation: CancellationToken,
    deadline: float,
    can_start_model: Callable[[], bool] = lambda: True,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> ProjectionResult:
    """Run transient retries plus one structured repair, else close deterministically."""

    if cancellation.cancelled or not can_start_model():
        return ProjectionResult(stale_projection(request), 0, 0, True)
    total_attempts = 0

    async def invoke(repair: tuple[str, ...]) -> WorkingProjection:
        nonlocal total_attempts

        async def operation(attempt: int) -> WorkingProjection:
            nonlocal total_attempts
            del attempt
            total_attempts += 1
            return await model(request, repair_violations=repair)

        result = await retry_async(
            operation,
            policy=retry_policy,
            retry_on=transient_errors,
            cancellation=cancellation,
            deadline=deadline,
            sleep=sleep or asyncio.sleep,
        )
        return result.value

    try:
        candidate = await invoke(())
        valid = validate_projection(candidate, request, state)
        return ProjectionResult(valid, 1, total_attempts, False)
    except ProjectionValidationError as first:
        if cancellation.cancelled or not can_start_model():
            return ProjectionResult(stale_projection(request), 1, total_attempts, True)
        try:
            repaired = await invoke(first.violations)
            valid = validate_projection(repaired, request, state)
        except (ProjectionValidationError, TransientExhaustedError, OperationCancelledError):
            return ProjectionResult(stale_projection(request), 2, total_attempts, True)
        return ProjectionResult(valid, 2, total_attempts, False)
    except (TransientExhaustedError, OperationCancelledError):
        return ProjectionResult(stale_projection(request), 1, total_attempts, True)


__all__ = [
    "ProjectionModel",
    "ProjectionResult",
    "ProjectionValidationError",
    "run_projection",
    "stale_projection",
    "validate_projection",
]
