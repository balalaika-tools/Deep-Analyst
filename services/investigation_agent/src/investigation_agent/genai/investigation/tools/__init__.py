"""Compose the investigation agent's capability-owned tools."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from investigation_agent.application.find_connections import FindConnections
from investigation_agent.genai.evidence_search.agent import SearchEvidenceAgent
from investigation_agent.genai.investigation.tools.find_connections import (
    build_find_connections_tool,
)
from investigation_agent.genai.investigation.tools.query_records import (
    build_query_records_tool,
)
from investigation_agent.genai.investigation.tools.search_evidence import (
    build_search_evidence_tool,
)
from investigation_agent.genai.record_query.agent import QueryRecordsAgent
from investigation_agent.genai.shared.retry import RetryPolicy

TOOL_NAMES: tuple[str, ...] = ("search_evidence", "query_records", "find_connections")


@dataclass(frozen=True, slots=True)
class ToolDependencies:
    search: SearchEvidenceAgent
    query: QueryRecordsAgent
    connections: FindConnections
    retry_policy: RetryPolicy
    transient_errors: tuple[type[BaseException], ...]


def build_investigation_tools(deps: ToolDependencies) -> list[BaseTool]:
    return [
        build_search_evidence_tool(deps.search),
        build_query_records_tool(deps.query),
        build_find_connections_tool(
            deps.connections,
            retry_policy=deps.retry_policy,
            transient_errors=deps.transient_errors,
        ),
    ]


__all__ = ["TOOL_NAMES", "ToolDependencies", "build_investigation_tools"]
