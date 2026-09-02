"""Turn-close projection, history commit, and message cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.history import (
    TurnStatus,
    append_assistant_message,
    set_turn_status,
    stable_assistant_message_id,
)
from investigation_agent.domain.investigation_state import (
    EvidenceCard,
    ExhaustedLimit,
    InvestigationState,
    TurnState,
    UsageCounters,
    state_update,
)
from investigation_agent.genai.investigation.grounding import (
    citations_for,
    deterministic_violations,
)
from investigation_agent.genai.investigation.middleware.contracts import (
    Clock,
    now_utc,
    require_state,
)
from investigation_agent.genai.investigation.schemas import AnswerDraft
from investigation_agent.genai.shared.retries import OperationCancelledError
from investigation_agent.genai.shared.structured import (
    StructuredResultRunner,
    cancellation_token,
    loop_deadline,
)
from investigation_agent.genai.state_projection.compactor import ProjectionModel, run_projection
from investigation_agent.genai.state_projection.schemas import TurnOutcome, build_projection_input
from investigation_agent.observability.instrumentation import phase_span


class TurnCloseMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Close a turn after optional fallback closure and projection refresh."""

    def __init__(
        self,
        *,
        closure: StructuredResultRunner[AnswerDraft] | None,
        projection_model: ProjectionModel | None,
        retry_policy: Any,
        transient_errors: tuple[type[BaseException], ...],
        closure_reserve: int,
        max_answer_chars: int,
        main_model_call_limit: int,
        main_tool_call_limit: int,
        clock: Clock = now_utc,
    ) -> None:
        if closure_reserve < 0:
            raise ValueError("closure_reserve cannot be negative")
        self._closure = closure
        self._projection_model = projection_model
        self._retry_policy = retry_policy
        self._transient_errors = transient_errors
        self._closure_reserve = closure_reserve
        self._max_answer_chars = max_answer_chars
        self._model_limit = main_model_call_limit
        self._tool_limit = main_tool_call_limit
        self._clock = clock

    async def aafter_agent(
        self, state: dict[str, Any], runtime: Runtime[RuntimeContext]
    ) -> dict[str, Any] | None:
        runtime.stream_writer({"phase": "committing_answer"})
        with phase_span("turn_close"):
            return await self._after_agent(state, runtime)

    async def _after_agent(
        self, state: dict[str, Any], runtime: Runtime[RuntimeContext]
    ) -> dict[str, Any] | None:
        parsed, turn = require_state(state)
        if turn.status in (TurnStatus.COMPLETED, TurnStatus.FAILED):
            return None
        (
            turn,
            answer,
            citations,
            answer_kind,
            failure_code,
            reserve_used,
        ) = await self._resolve_answer(parsed, turn, state, runtime.context)
        outcome: TurnOutcome = (
            "completed"
            if answer is not None and answer_kind != "refusal"
            else "refused"
            if answer is not None
            else "failed"
        )
        projection, projection_calls = await self._refresh_projection(
            parsed,
            turn,
            runtime.context,
            outcome=outcome,
            answer=answer,
            failure_code=failure_code,
            reserve_left=self._closure_reserve - reserve_used,
        )
        turn, history = self._commit(
            parsed,
            turn,
            answer=answer,
            citations=citations,
            answer_kind=answer_kind,
            failure_code=failure_code,
        )
        reserve_used += projection_calls
        usage = UsageCounters(model_calls=reserve_used, closure_model_calls=reserve_used)
        return {
            **state_update(turn=turn, history=history, projection=projection),
            "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
            "usage": usage.model_dump(mode="json"),
            "structured_response": None,
        }

    async def _resolve_answer(
        self,
        parsed: InvestigationState,
        turn: TurnState,
        raw_state: Mapping[str, Any],
        context: RuntimeContext,
    ) -> tuple[TurnState, str | None, tuple[Any, ...], str | None, str | None, int]:
        answer = turn.pending_answer
        citations = turn.pending_citations
        answer_kind = turn.answer_kind
        failure_code = turn.safe_failure_code
        reserve_used = 0
        if answer is None and failure_code is None:
            turn = turn.model_copy(
                update={"exhausted_limit": self._exhausted_limit(raw_state, context)}
            )
            closure, attempted = await self._closure_answer(
                parsed, context, reserve_left=self._closure_reserve
            )
            reserve_used += attempted
            if closure is None:
                failure_code = "budget_exhausted"
            else:
                answer, citations = closure
                answer_kind = "closure"
        return turn, answer, citations, answer_kind, failure_code, reserve_used

    def _commit(
        self,
        state: InvestigationState,
        turn: TurnState,
        *,
        answer: str | None,
        citations: tuple[Any, ...],
        answer_kind: str | None,
        failure_code: str | None,
    ) -> tuple[TurnState, Any]:
        history = state.history
        if answer is None:
            if any(message.turn_id == turn.turn_id for message in history.messages):
                history = set_turn_status(history, turn.turn_id, TurnStatus.FAILED)
            return turn.model_copy(
                update={
                    "status": TurnStatus.FAILED,
                    "safe_failure_code": failure_code or "internal",
                }
            ), history
        assistant_id = stable_assistant_message_id(turn.turn_id)
        history = append_assistant_message(
            history,
            message_id=assistant_id,
            turn_id=turn.turn_id,
            request_id=turn.request_id,
            content=answer,
            citations=citations,
            status=TurnStatus.COMPLETED,
            created_at=self._clock(),
        )
        return turn.model_copy(
            update={
                "status": TurnStatus.COMPLETED,
                "assistant_message_id": assistant_id,
                "answer_kind": answer_kind,
                "pending_answer": answer,
                "pending_citations": citations,
                "safe_failure_code": None,
            }
        ), history

    def _exhausted_limit(self, state: Mapping[str, Any], context: RuntimeContext) -> ExhaustedLimit:
        if int(state.get("run_model_call_count", 0) or 0) >= self._model_limit:
            return "model_calls"
        tool_counts = state.get("run_tool_call_count") or {}
        if (
            isinstance(tool_counts, Mapping)
            and int(tool_counts.get("__all__", 0) or 0) >= self._tool_limit
        ):
            return "tool_calls"
        return "elapsed" if context.remaining_seconds() <= 0 else "recursion"

    async def _closure_answer(
        self, state: InvestigationState, context: RuntimeContext, *, reserve_left: int
    ) -> tuple[tuple[str, tuple[Any, ...]] | None, int]:
        if (
            self._closure is None
            or reserve_left < 1
            or context.cancellation.cancelled
            or context.remaining_seconds() <= 0
        ):
            return None, 0
        try:
            result = await self._closure.run(
                {"evidence_cards": [_card_payload(card) for card in state.evidence.ordered()]},
                context=context,
            )
        except (asyncio.CancelledError, OperationCancelledError):
            raise
        except Exception:
            return None, 1
        draft = result.value
        violations = deterministic_violations(
            draft, state, max_answer_chars=self._max_answer_chars, coverage_incomplete=True
        )
        return (None, 1) if violations else ((draft.answer, citations_for(draft, state)), 1)

    async def _refresh_projection(
        self,
        state: InvestigationState,
        turn: TurnState,
        context: RuntimeContext,
        *,
        outcome: TurnOutcome,
        answer: str | None,
        failure_code: str | None,
        reserve_left: int,
    ) -> tuple[Any, int]:
        evidence_added = tuple(
            card for card in state.evidence.ordered() if card.first_seen_turn_id == turn.turn_id
        )
        if outcome != "completed":
            stale = state.projection.projection_stale or bool(evidence_added)
            return state.projection.model_copy(update={"projection_stale": stale}), 0
        request = build_projection_input(
            source_turn_id=turn.turn_id,
            utterance=turn.utterance,
            predecessor=state.projection,
            evidence_added=evidence_added,
            outcome=outcome,
            answer=answer,
            failure_code=failure_code,
            coverage_incomplete=state.evidence.coverage_notice is not None
            or outcome != "completed",
        )
        if self._projection_model is None:
            return request.predecessor.model_copy(update={"projection_stale": True}), 0
        result = await run_projection(
            request,
            state,
            model=self._projection_model,
            retry_policy=self._retry_policy,
            transient_errors=self._transient_errors,
            cancellation=cancellation_token(context),
            deadline=loop_deadline(context),
            can_start_model=lambda: (
                reserve_left > 0
                and not context.cancellation.cancelled
                and context.remaining_seconds() > 0
            ),
        )
        return result.projection, min(result.model_calls, max(reserve_left, 0))


def _card_payload(card: EvidenceCard) -> dict[str, object]:
    fields = "\n".join(f"{field.name}: {field.value}" for field in card.fields)
    visible = "\n".join(part for part in (card.display or "", fields) if part)
    label = "suspicious-untrusted-evidence" if card.suspicious_content else "untrusted-evidence"
    return {
        "evidence_id": card.evidence_id,
        "kind": card.kind,
        "evidentiary_status": card.evidentiary_status,
        "content": f"<{label}>\n{visible}\n</{label}>",
    }


__all__ = ["TurnCloseMiddleware"]
