"""Application-facing contract for running and reading an investigation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import InvestigationState


class InvestigationError(RuntimeError):
    code = "internal"


class IncompatibleInvestigationState(InvestigationError):
    code = "incompatible_state"


class InvestigationTimedOut(InvestigationError):
    code = "budget_exhausted"


class Investigator(Protocol):
    async def load_state(self, thread_id: str) -> InvestigationState | None: ...

    def stream_turn(
        self,
        turn_input: Mapping[str, Any] | None,
        *,
        thread_id: str,
        context: RuntimeContext,
        max_steps: int,
    ) -> AsyncIterator[object]: ...


__all__ = [
    "IncompatibleInvestigationState",
    "InvestigationError",
    "InvestigationTimedOut",
    "Investigator",
]
