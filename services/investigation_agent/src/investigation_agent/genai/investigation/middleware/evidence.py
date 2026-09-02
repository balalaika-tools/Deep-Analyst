"""Evidence-boundary middleware and compact tool-result rendering."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from investigation_agent.core.context import RuntimeContext
from investigation_agent.core.errors import BudgetExhaustedFailure
from investigation_agent.domain.investigation_state import UsageCounters, upsert_evidence
from investigation_agent.domain.tool_outcome import BudgetConsumption, OutcomeStatus, ToolOutcome
from investigation_agent.genai.guardrails.middleware import deterministic_evidence_boundary
from investigation_agent.genai.guardrails.schemas import GuardedEvidenceBatch, NormalizedEvidence
from investigation_agent.genai.investigation.middleware.contracts import (
    EvidenceGuard,
    require_state,
)
from investigation_agent.genai.shared.retries import (
    OperationCancelledError,
    TransientExhaustedError,
)

_INCOMPLETE_STATUSES = frozenset(
    {
        OutcomeStatus.NO_RETRIEVED_SUPPORT,
        OutcomeStatus.RETRIEVAL_INCOMPLETE,
        OutcomeStatus.QUERY_EXHAUSTED,
        OutcomeStatus.NO_SUPPORT,
        OutcomeStatus.TRANSIENT_EXHAUSTED,
        OutcomeStatus.BUDGET_EXHAUSTED,
        OutcomeStatus.DEPENDENCY_UNAVAILABLE,
        OutcomeStatus.CANCELLED,
    }
)


class EvidenceIndexMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Guard tool evidence, update the trusted index, and return compact messages."""

    def __init__(
        self,
        *,
        max_cards: int,
        evidence_guard: EvidenceGuard | None = None,
        max_rendered_chars: int = 2_000,
    ) -> None:
        if max_cards < 1:
            raise ValueError("max_cards must be positive")
        self._max_cards = max_cards
        self._guard = evidence_guard
        self._max_rendered_chars = max_rendered_chars

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        context = request.runtime.context
        if context is None:
            raise RuntimeError("tool execution requires the trusted runtime context")
        parsed, turn = require_state(request.state)
        tool_call_id = str(request.tool_call["id"])
        tool_name = str(request.tool_call["name"])
        result = await self._execute(request, handler, context, tool_call_id, tool_name)
        if isinstance(result, Command):
            return result
        outcome = result.artifact if isinstance(result.artifact, ToolOutcome) else None
        if outcome is None:
            return self._failed(tool_call_id, tool_name, OutcomeStatus.VALIDATION_FAILED)
        outcome, guard_consumption = await self._guarded(outcome, context)
        index = upsert_evidence(
            parsed.evidence,
            outcome,
            turn_id=turn.turn_id,
            max_cards=self._max_cards,
            protected_ids=parsed.projection.referenced_evidence_ids(),
        )
        usage = UsageCounters().consume(outcome.consumption).consume(guard_consumption)
        usage = usage.model_copy(update={"tool_calls": usage.tool_calls + 1})
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=render_tool_message(outcome, max_chars=self._max_rendered_chars),
                        tool_call_id=tool_call_id,
                        name=tool_name,
                    )
                ],
                "evidence": index.model_dump(mode="json"),
                "usage": usage.model_dump(mode="json"),
            }
        )

    async def _execute(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
        context: RuntimeContext,
        tool_call_id: str,
        tool_name: str,
    ) -> ToolMessage | Command[Any]:
        try:
            context.check_active()
            return await handler(request)
        except (asyncio.CancelledError, OperationCancelledError):
            raise
        except TransientExhaustedError:
            return self._failed(tool_call_id, tool_name, OutcomeStatus.TRANSIENT_EXHAUSTED)
        except (TimeoutError, BudgetExhaustedFailure):
            return self._failed(tool_call_id, tool_name, OutcomeStatus.BUDGET_EXHAUSTED)
        except Exception:
            return self._failed(tool_call_id, tool_name, OutcomeStatus.DEPENDENCY_UNAVAILABLE)

    async def _guarded(
        self, outcome: ToolOutcome, context: RuntimeContext
    ) -> tuple[ToolOutcome, BudgetConsumption]:
        if not outcome.evidence:
            return outcome, BudgetConsumption()
        guarded, consumption, warnings = await self._run_guard(outcome, context)
        verdicts = {item.evidence_id: item for item in guarded}
        if set(verdicts) != {item.evidence_id for item in outcome.evidence}:
            raise ValueError("evidence guardrail changed the evidence identifier set")
        evidence = tuple(
            item.model_copy(
                update={
                    "suspicious_content": verdicts[item.evidence_id].suspicious,
                    "guard_status": verdicts[item.evidence_id].guard_status,
                }
            )
            for item in outcome.evidence
        )
        return outcome.model_copy(
            update={"evidence": evidence, "warnings": (*outcome.warnings, *warnings)}
        ), consumption

    async def _run_guard(
        self, outcome: ToolOutcome, context: RuntimeContext
    ) -> tuple[tuple[NormalizedEvidence, ...], BudgetConsumption, tuple[str, ...]]:
        if self._guard is None:
            return deterministic_evidence_boundary(outcome.evidence), BudgetConsumption(), ()
        try:
            verdict = await self._guard(outcome.evidence, context)
        except (asyncio.CancelledError, OperationCancelledError):
            raise
        except Exception as exc:
            attempts = getattr(exc, "attempts", 0)
            consumption = (
                BudgetConsumption(model_calls=attempts, physical_attempts=attempts)
                if isinstance(attempts, int) and attempts > 0
                else BudgetConsumption()
            )
            return (
                deterministic_evidence_boundary(outcome.evidence),
                consumption,
                ("evidence_guardrail_fallback",),
            )
        if isinstance(verdict, GuardedEvidenceBatch):
            return (
                verdict.items,
                BudgetConsumption(
                    model_calls=verdict.model_calls,
                    physical_attempts=verdict.physical_attempts,
                ),
                (),
            )
        return verdict, BudgetConsumption(), ()

    @staticmethod
    def _failed(tool_call_id: str, tool_name: str, status: OutcomeStatus) -> Command[Any]:
        content = json.dumps(
            {"tool": tool_name, "status": status.value, "evidence": []}, sort_keys=True
        )
        return Command(
            update={
                "messages": [
                    ToolMessage(content=content, tool_call_id=tool_call_id, name=tool_name)
                ],
                "usage": UsageCounters(tool_calls=1).model_dump(mode="json"),
            }
        )


def render_tool_message(outcome: ToolOutcome, *, max_chars: int = 2_000) -> str:
    """Render only bounded, delimited evidence for the next model call."""

    header = {
        "tool": outcome.tool,
        "status": outcome.status.value,
        "attempts": len(outcome.attempts),
        "warnings": list(outcome.warnings),
        "evidence_ids": [item.evidence_id for item in outcome.evidence],
    }
    parts = [json.dumps(header, sort_keys=True)]
    for item in deterministic_evidence_boundary(outcome.evidence):
        rendered = item.rendered
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars] + "\n[trimmed]</untrusted-evidence>"
        parts.append(rendered)
    return "\n".join(parts)


def coverage_incomplete_from_messages(messages: Sequence[AnyMessage]) -> bool:
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            status = json.loads(str(message.content).split("\n", 1)[0]).get("status")
        except (ValueError, AttributeError):
            continue
        if status in {item.value for item in _INCOMPLETE_STATUSES}:
            return True
    return False


__all__ = [
    "EvidenceGuard",
    "EvidenceIndexMiddleware",
    "coverage_incomplete_from_messages",
    "render_tool_message",
]
