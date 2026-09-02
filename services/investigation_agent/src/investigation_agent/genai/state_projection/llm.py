"""Structured model adapter for turn-close projection replacement."""

from __future__ import annotations

from typing import Any

from investigation_agent.domain.investigation_state import WorkingProjection
from investigation_agent.genai.shared.structured import StructuredChat
from investigation_agent.genai.state_projection.prompts import PROJECTION_SYSTEM_PROMPT
from investigation_agent.genai.state_projection.schemas import ProjectionInput


class ProjectionModelRunner:
    def __init__(self, model: Any) -> None:
        self._chat = StructuredChat(model, WorkingProjection, PROJECTION_SYSTEM_PROMPT)

    async def __call__(
        self, request: ProjectionInput, *, repair_violations: tuple[str, ...] = ()
    ) -> WorkingProjection:
        return WorkingProjection.model_validate(
            await self._chat.invoke(
                {
                    "projection_input": request.model_dump(mode="json"),
                    "repair_violations": list(repair_violations),
                }
            )
        )


__all__ = ["ProjectionModelRunner"]
