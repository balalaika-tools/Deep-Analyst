"""Public middleware surface for the investigation agent."""

from investigation_agent.genai.investigation.middleware.context import (
    ContextMiddleware,
    build_system_prompt,
    trim_turn_messages,
)
from investigation_agent.genai.investigation.middleware.evidence import (
    EvidenceGuard,
    EvidenceIndexMiddleware,
    coverage_incomplete_from_messages,
    render_tool_message,
)
from investigation_agent.genai.investigation.middleware.grounding import GroundingMiddleware
from investigation_agent.genai.investigation.middleware.intake import TurnIntakeMiddleware
from investigation_agent.genai.investigation.middleware.model_failures import (
    SAFE_FAILURE_KEY,
    ModelFailureMiddleware,
)
from investigation_agent.genai.investigation.middleware.turn_close import TurnCloseMiddleware

__all__ = [
    "SAFE_FAILURE_KEY",
    "ContextMiddleware",
    "EvidenceGuard",
    "EvidenceIndexMiddleware",
    "GroundingMiddleware",
    "ModelFailureMiddleware",
    "TurnCloseMiddleware",
    "TurnIntakeMiddleware",
    "build_system_prompt",
    "coverage_incomplete_from_messages",
    "render_tool_message",
    "trim_turn_messages",
]
