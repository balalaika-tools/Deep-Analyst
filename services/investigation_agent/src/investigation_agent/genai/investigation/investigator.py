"""LangGraph-backed implementation of the investigation capability."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import (
    IncompatibleStateError,
    InvestigationState,
    parse_state,
)
from investigation_agent.ports.checkpoints import CHECKPOINT_APP
from investigation_agent.ports.investigator import (
    IncompatibleInvestigationState,
    InvestigationError,
    InvestigationTimedOut,
)


class _GraphSnapshot(Protocol):
    @property
    def values(self) -> Mapping[str, Any]: ...


class _AgentHarness(Protocol):
    async def aget_state(self, config: Mapping[str, object]) -> _GraphSnapshot: ...

    def astream(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, object],
        *,
        context: RuntimeContext,
        stream_mode: list[str],
        durability: str,
        version: str,
    ) -> AsyncIterator[object]: ...


class LangGraphInvestigator:
    def __init__(self, harness: _AgentHarness) -> None:
        self._harness = harness

    async def load_state(self, thread_id: str) -> InvestigationState | None:
        try:
            snapshot = await self._harness.aget_state(_graph_config(thread_id=thread_id))
            return parse_state(snapshot.values)
        except IncompatibleStateError:
            raise IncompatibleInvestigationState from None
        except InvestigationError:
            raise
        except TimeoutError:
            raise InvestigationTimedOut from None
        except Exception as error:
            raise InvestigationError from error

    async def stream_turn(
        self,
        turn_input: Mapping[str, Any] | None,
        *,
        thread_id: str,
        context: RuntimeContext,
        max_steps: int,
    ) -> AsyncIterator[object]:
        try:
            async for event in self._harness.astream(
                turn_input,
                _graph_config(thread_id=thread_id, recursion_limit=max_steps),
                context=context,
                stream_mode=["updates", "custom"],
                durability="sync",
                version="v2",
            ):
                yield event
        except InvestigationError:
            raise
        except TimeoutError:
            raise InvestigationTimedOut from None
        except Exception as error:
            raise InvestigationError from error


def _graph_config(*, thread_id: str, recursion_limit: int = 200) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": {"app": CHECKPOINT_APP, "public_thread_id": thread_id},
        "recursion_limit": recursion_limit,
    }


__all__ = ["LangGraphInvestigator"]
