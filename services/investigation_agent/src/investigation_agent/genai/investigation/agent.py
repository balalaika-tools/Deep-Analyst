"""The single ``create_agent`` that owns every investigation turn."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools import BaseTool

from investigation_agent.core.context import RuntimeContext
from investigation_agent.genai.guardrails.middleware import (
    GuardrailEvaluator,
    InputGuardrailMiddleware,
)
from investigation_agent.genai.investigation.middleware import (
    ContextMiddleware,
    EvidenceGuard,
    EvidenceIndexMiddleware,
    GroundingMiddleware,
    ModelFailureMiddleware,
    TurnCloseMiddleware,
    TurnIntakeMiddleware,
)
from investigation_agent.genai.investigation.prompts import MAIN_SYSTEM_PROMPT
from investigation_agent.genai.investigation.schemas import (
    AnswerDraft,
    GroundingVerdict,
    InvestigationAgentState,
)
from investigation_agent.genai.investigation.tools import TOOL_NAMES
from investigation_agent.genai.shared.retries import RetryPolicy, model_retry_middleware
from investigation_agent.genai.shared.structured import StructuredResultRunner
from investigation_agent.genai.state_projection.compactor import ProjectionModel

EXPECTED_NODE_NAMES: frozenset[str] = frozenset(
    {
        "TurnIntakeMiddleware.before_agent",
        "InputGuardrailMiddleware.before_agent",
        "ModelCallLimitMiddleware.before_model",
        "ModelCallLimitMiddleware.after_model",
        "ToolCallLimitMiddleware.after_model",
        "model",
        "tools",
        "GroundingMiddleware.after_model",
        "TurnCloseMiddleware.after_agent",
    }
)


@dataclass(frozen=True, slots=True)
class AgentLimits:
    main_model_call_limit: int
    main_tool_call_limit: int
    closure_model_calls: int
    max_context_tokens: int
    max_answer_chars: int
    max_evidence_cards: int
    max_history_turns: int

    def __post_init__(self) -> None:
        if self.closure_model_calls >= self.main_model_call_limit:
            raise ValueError("closure reserve must be smaller than the main model-call limit")
        if min(self.main_tool_call_limit, self.max_context_tokens, self.max_answer_chars) < 1:
            raise ValueError("agent limits must be positive")

    @property
    def loop_model_calls(self) -> int:
        """Model calls available to the ReAct loop; the remainder is the closure reserve."""

        return self.main_model_call_limit - self.closure_model_calls


@dataclass(frozen=True, slots=True)
class AgentComponents:
    model: Any
    tools: Sequence[BaseTool]
    guardrail: GuardrailEvaluator
    evidence_guard: EvidenceGuard | None
    verifier: StructuredResultRunner[GroundingVerdict] | None
    closure: StructuredResultRunner[AnswerDraft] | None
    projection_model: ProjectionModel | None
    retry_policy: RetryPolicy
    transient_errors: tuple[type[Exception], ...]
    telemetry: Sequence[AgentMiddleware[Any, Any, Any]] = ()


def build_investigation_agent(
    components: AgentComponents, *, limits: AgentLimits, checkpointer: Any
) -> Any:
    """Compose the middleware stack in design order around one ``create_agent``."""

    names = {tool.name for tool in components.tools}
    if names != set(TOOL_NAMES):
        raise ValueError(f"investigation agent requires exactly the tools {TOOL_NAMES}")
    middleware: list[AgentMiddleware[Any, Any, Any]] = [
        TurnIntakeMiddleware(max_history_turns=limits.max_history_turns),
        InputGuardrailMiddleware(components.guardrail),
        ModelFailureMiddleware(),
        model_retry_middleware(components.retry_policy, components.transient_errors),
        cast(
            Any,
            ModelCallLimitMiddleware(run_limit=limits.loop_model_calls, exit_behavior="end"),
        ),
        cast(
            Any,
            ToolCallLimitMiddleware(run_limit=limits.main_tool_call_limit, exit_behavior="end"),
        ),
        ContextMiddleware(
            instructions=MAIN_SYSTEM_PROMPT, max_context_tokens=limits.max_context_tokens
        ),
        EvidenceIndexMiddleware(
            max_cards=limits.max_evidence_cards, evidence_guard=components.evidence_guard
        ),
        GroundingMiddleware(verifier=components.verifier, max_answer_chars=limits.max_answer_chars),
        TurnCloseMiddleware(
            closure=components.closure,
            projection_model=components.projection_model,
            retry_policy=components.retry_policy,
            transient_errors=components.transient_errors,
            closure_reserve=limits.closure_model_calls,
            max_answer_chars=limits.max_answer_chars,
            main_model_call_limit=limits.loop_model_calls,
            main_tool_call_limit=limits.main_tool_call_limit,
        ),
        *components.telemetry,
    ]
    return create_agent(
        model=components.model,
        tools=list(components.tools),
        state_schema=InvestigationAgentState,
        context_schema=RuntimeContext,
        # Let LangChain select provider-native structured output when the model
        # advertises it. Explicit ToolStrategy forces tool_choice="any", which
        # Amazon Bedrock does not support for every model (including Terra).
        response_format=AnswerDraft,
        checkpointer=checkpointer,
        middleware=middleware,
    )


__all__ = [
    "EXPECTED_NODE_NAMES",
    "AgentComponents",
    "AgentLimits",
    "build_investigation_agent",
]
