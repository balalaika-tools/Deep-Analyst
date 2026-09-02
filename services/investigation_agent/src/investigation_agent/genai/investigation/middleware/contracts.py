"""Shared contracts and state preconditions for investigation middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import (
    InvestigationState,
    TurnState,
    parse_state,
)
from investigation_agent.genai.guardrails.schemas import GuardedEvidenceBatch, NormalizedEvidence

type Clock = Callable[[], datetime]
type EvidenceGuard = Callable[
    [tuple[Any, ...], RuntimeContext],
    Awaitable[tuple[NormalizedEvidence, ...] | GuardedEvidenceBatch],
]


def now_utc() -> datetime:
    return datetime.now(UTC)


def require_state(state: Mapping[str, Any]) -> tuple[InvestigationState, TurnState]:
    parsed = parse_state(state)
    if parsed is None or parsed.turn is None:
        raise RuntimeError("investigation hooks require control state and an open turn")
    return parsed, parsed.turn


__all__ = ["Clock", "EvidenceGuard", "now_utc", "require_state"]
