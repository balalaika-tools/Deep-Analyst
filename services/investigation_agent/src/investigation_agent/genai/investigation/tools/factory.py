"""Bind the three global-corpus investigation tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import parse_state
from investigation_agent.domain.tool_outcome import ToolOutcome
from investigation_agent.genai.evidence_search.agent import SearchEvidenceAgent
from investigation_agent.genai.evidence_search.schemas import SearchIntent
from investigation_agent.genai.investigation.connections import (
    FindConnections,
    FindConnectionsInput,
    FindConnectionsOutcome,
)
from investigation_agent.genai.investigation.tools.outcomes import (
    connections_outcome,
    query_outcome,
    search_outcome,
)
from investigation_agent.genai.record_query.agent import QueryRecordsAgent
from investigation_agent.genai.record_query.schemas import QueryIntent
from investigation_agent.genai.shared.retries import RetryPolicy, retry_async
from investigation_agent.genai.shared.structured import cancellation_token, loop_deadline

TOOL_NAMES: tuple[str, ...] = ("search_evidence", "query_records", "find_connections")
_PROGRESS_KEYS = frozenset({"phase", "tool", "attempt", "count"})


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    search: SearchEvidenceAgent
    query: QueryRecordsAgent
    connections: FindConnections
    retry_policy: RetryPolicy
    transient_errors: tuple[type[BaseException], ...]


def build_investigation_tools(deps: ToolDependencies) -> list[BaseTool]:
    """Bind nested agents and traversal; trusted scope arrives at call time."""

    @tool(response_format="content_and_artifact")
    async def search_evidence(
        intent: SearchIntent, runtime: ToolRuntime[RuntimeContext, Any]
    ) -> tuple[str, ToolOutcome]:
        """Hybrid lexical and semantic search over the global text evidence."""

        context = runtime.context
        context.check_active()
        progress = _progress_writer(runtime, tool="search_evidence")
        progress({"phase": "searching_evidence", "tool": "search_evidence", "attempt": 1})
        raw = await deps.search.run(
            intent,
            call_id=runtime.tool_call_id or "search_evidence",
            deadline=loop_deadline(context),
            cancellation=cancellation_token(context),
            seen_chunk_ids=_seen_chunk_ids(runtime.state),
            progress=progress,
        )
        outcome = search_outcome(raw, intent=intent)
        return outcome.status.value, outcome

    @tool(response_format="content_and_artifact")
    async def query_records(
        intent: QueryIntent, runtime: ToolRuntime[RuntimeContext, Any]
    ) -> tuple[str, ToolOutcome]:
        """Query global structured records through policy-gated SQL."""

        context = runtime.context
        context.check_active()
        progress = _progress_writer(runtime, tool="query_records")
        progress({"phase": "querying_records", "tool": "query_records", "attempt": 1})
        raw = await deps.query.run(
            intent,
            call_id=runtime.tool_call_id or "query_records",
            deadline=loop_deadline(context),
            cancellation=cancellation_token(context),
            progress=progress,
        )
        outcome = query_outcome(raw, intent=intent)
        return outcome.status.value, outcome

    @tool(response_format="content_and_artifact")
    async def find_connections(
        request: FindConnectionsInput, runtime: ToolRuntime[RuntimeContext, Any]
    ) -> tuple[str, ToolOutcome]:
        """Traverse sourced global relationships within server-owned bounds."""

        context = runtime.context
        context.check_active()
        progress = _progress_writer(runtime, tool="find_connections")
        progress({"phase": "finding_connections", "tool": "find_connections", "attempt": 1})
        call_id = runtime.tool_call_id or "find_connections"
        deadline = loop_deadline(context)

        async def read(attempt: int) -> FindConnectionsOutcome:
            del attempt
            return await deps.connections.run(call_id=call_id, request=request, deadline=deadline)

        result = await retry_async(
            read,
            policy=deps.retry_policy,
            retry_on=tuple(deps.transient_errors),
            cancellation=cancellation_token(context),
            deadline=deadline,
        )
        outcome = connections_outcome(
            result.value,
            request=request,
            physical_attempts=result.attempts,
        )
        return outcome.status.value, outcome

    return [search_evidence, query_records, find_connections]


def _progress_writer(
    runtime: ToolRuntime[RuntimeContext, Any], *, tool: str
) -> Callable[[Mapping[str, object]], None]:
    def write(data: Mapping[str, object]) -> None:
        safe = {key: value for key, value in data.items() if key in _PROGRESS_KEYS}
        safe.setdefault("tool", tool)
        try:
            runtime.stream_writer(safe)
        except RuntimeError:
            return

    return write


def _seen_chunk_ids(state: Mapping[str, Any] | None) -> frozenset[str]:
    parsed = parse_state(state)
    if parsed is None:
        return frozenset()
    return frozenset(
        card.evidence_id for card in parsed.evidence.cards.values() if card.kind == "chunk"
    )


__all__ = ["TOOL_NAMES", "ToolDependencies", "build_investigation_tools"]
