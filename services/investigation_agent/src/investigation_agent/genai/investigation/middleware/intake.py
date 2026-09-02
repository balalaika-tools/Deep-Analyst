"""Turn-intake middleware."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.history import (
    HistoryState,
    ThreadFullError,
    TurnStatus,
    append_user_message,
    set_turn_status,
)
from investigation_agent.domain.investigation_state import state_update
from investigation_agent.genai.investigation.middleware.contracts import require_state


class TurnIntakeMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Open a turn exactly once and reset the framework message channel."""

    def __init__(self, *, max_history_turns: int) -> None:
        if max_history_turns < 1:
            raise ValueError("max_history_turns must be positive")
        self._max_history_turns = max_history_turns

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self, state: dict[str, Any], runtime: Runtime[RuntimeContext]
    ) -> dict[str, Any] | None:
        del runtime
        parsed, turn = require_state(state)
        if turn.intake_complete:
            return None
        history = _mark_abandoned_turns(parsed.history, current_turn_id=turn.turn_id)
        try:
            history = append_user_message(
                history,
                message_id=turn.user_message_id,
                turn_id=turn.turn_id,
                request_id=turn.request_id,
                content=turn.utterance,
                created_at=turn.opened_at,
                max_turns=self._max_history_turns,
            )
        except ThreadFullError:
            failed = turn.model_copy(
                update={"intake_complete": True, "safe_failure_code": "thread_full"}
            )
            return {
                **state_update(turn=failed, history=history),
                "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)],
                "structured_response": None,
                "jump_to": "end",
            }
        opened = turn.model_copy(update={"intake_complete": True})
        return {
            **state_update(turn=opened, history=history),
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                HumanMessage(content=turn.utterance, id=turn.user_message_id),
            ],
            "structured_response": None,
        }


def _mark_abandoned_turns(history: HistoryState, *, current_turn_id: str) -> HistoryState:
    running = {
        message.turn_id
        for message in history.messages
        if message.turn_status is TurnStatus.RUNNING and message.turn_id != current_turn_id
    }
    for turn_id in sorted(running):
        history = set_turn_status(history, turn_id, TurnStatus.INTERRUPTED)
    return history


__all__ = ["TurnIntakeMiddleware"]
