"""The graph-connection tool and its agent-artifact translation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from investigation_agent.application.find_connections import FindConnections
from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.connections import FindConnectionsInput, FindConnectionsOutcome
from investigation_agent.domain.tool_outcome import (
    AttemptKind,
    AttemptRecord,
    BudgetConsumption,
    CoverageUpdate,
    EvidenceItem,
    EvidenceProvenance,
    OutcomeStatus,
    ToolOutcome,
    canonical_fingerprint,
)
from investigation_agent.genai.shared.retry import RetryPolicy, retry_async, retry_deadline

_PROGRESS_KEYS = frozenset({"phase", "tool", "attempt", "count"})

FIND_CONNECTIONS_TOOL_DESCRIPTION = """Traverse sourced relationships from exact entity IDs.
Discover seed IDs with search_evidence or query_records first; never pass names or invented IDs.
Predicates retain subject-to-object meaning even though traversal follows edges in either direction.
Use confirmed relationships by default, and include proposed relationships only for explicitly
labelled hypotheses. Filters can restrict predicates, occurrence time, and terminal entity types;
server-owned bounds may cap the requested traversal limits."""


def build_find_connections_tool(
    connections: FindConnections,
    *,
    retry_policy: RetryPolicy,
    transient_errors: tuple[type[BaseException], ...],
) -> BaseTool:
    @tool(
        response_format="content_and_artifact",
        description=FIND_CONNECTIONS_TOOL_DESCRIPTION,
    )
    async def find_connections(
        request: FindConnectionsInput, runtime: ToolRuntime[RuntimeContext, Any]
    ) -> tuple[str, ToolOutcome]:
        context = runtime.context
        context.check_active()
        progress = _progress_writer(runtime)
        progress({"phase": "finding_connections", "tool": "find_connections", "attempt": 1})
        call_id = runtime.tool_call_id or "find_connections"
        deadline = retry_deadline(context)

        async def read(attempt: int) -> FindConnectionsOutcome:
            del attempt
            return await connections.run(call_id=call_id, request=request, deadline=deadline)

        result = await retry_async(
            read,
            policy=retry_policy,
            retry_on=tuple(transient_errors),
            cancellation=context.cancellation,
            deadline=deadline,
        )
        outcome = _to_tool_outcome(
            result.value,
            request=request,
            physical_attempts=result.attempts,
        )
        return outcome.status.value, outcome

    return find_connections


def _to_tool_outcome(
    raw: FindConnectionsOutcome,
    *,
    request: FindConnectionsInput,
    physical_attempts: int,
) -> ToolOutcome:
    fingerprint = canonical_fingerprint({"tool": "find_connections", "intent": request})
    evidence = tuple(
        EvidenceItem(
            evidence_id=item.evidence_id,
            kind=item.kind,
            content_hash=item.content_hash,
            source_refs=item.source_refs,
            content=item.content,
            evidentiary_status=item.evidentiary_status,
            provenance=(EvidenceProvenance(attempt=1, modality="graph"),),
        )
        for item in raw.evidence
    )
    consumption = BudgetConsumption.model_validate(raw.consumption.model_dump())
    return ToolOutcome(
        call_id=raw.call_id,
        intent_fingerprint=fingerprint,
        tool="find_connections",
        status=OutcomeStatus(raw.status),
        attempts=(
            AttemptRecord(
                attempt=physical_attempts,
                operation_id=raw.intent_fingerprint[:32],
                kind=AttemptKind.GRAPH,
                outcome="succeeded",
            ),
        ),
        evidence=evidence,
        coverage=(
            CoverageUpdate(
                dimension="find_connections",
                status="complete" if raw.status == "connections_found" else "incomplete",
                digest=canonical_fingerprint(
                    {"tool": "find_connections", "status": raw.status, "intent": fingerprint}
                ),
            ),
        ),
        warnings=raw.warnings,
        consumption=consumption.model_copy(
            update={"physical_attempts": consumption.physical_attempts + physical_attempts - 1}
        ),
    )


def _progress_writer(
    runtime: ToolRuntime[RuntimeContext, Any],
) -> Callable[[Mapping[str, object]], None]:
    def write(data: Mapping[str, object]) -> None:
        safe = {key: value for key, value in data.items() if key in _PROGRESS_KEYS}
        safe.setdefault("tool", "find_connections")
        try:
            runtime.stream_writer(safe)
        except RuntimeError:
            return

    return write


__all__ = ["FIND_CONNECTIONS_TOOL_DESCRIPTION", "build_find_connections_tool"]
