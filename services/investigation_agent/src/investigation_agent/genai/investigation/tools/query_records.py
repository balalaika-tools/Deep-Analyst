"""The structured-record query tool and its agent-artifact translation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.tool_outcome import (
    AttemptKind,
    AttemptRecord,
    BudgetConsumption,
    CoverageUpdate,
    EvidenceField,
    EvidenceItem,
    EvidenceProvenance,
    OutcomeStatus,
    ToolOutcome,
    canonical_fingerprint,
)
from investigation_agent.genai.record_query.agent import QueryRecordsAgent
from investigation_agent.genai.record_query.schemas import QueryIntent, QueryOutcome
from investigation_agent.genai.shared.retry import retry_deadline

_PROGRESS_KEYS = frozenset({"phase", "tool", "attempt", "count"})


def build_query_records_tool(query: QueryRecordsAgent) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def query_records(
        intent: QueryIntent, runtime: ToolRuntime[RuntimeContext, Any]
    ) -> tuple[str, ToolOutcome]:
        """Query global structured records through policy-gated SQL."""

        context = runtime.context
        context.check_active()
        progress = _progress_writer(runtime)
        progress({"phase": "querying_records", "tool": "query_records", "attempt": 1})
        raw = await query.run(
            intent,
            call_id=runtime.tool_call_id or "query_records",
            deadline=retry_deadline(context),
            cancellation=context.cancellation,
            progress=progress,
        )
        outcome = _to_tool_outcome(raw, intent=intent)
        return outcome.status.value, outcome

    return query_records


def _to_tool_outcome(raw: QueryOutcome, *, intent: QueryIntent) -> ToolOutcome:
    fingerprint = canonical_fingerprint({"tool": "query_records", "intent": intent})
    evidence = tuple(
        EvidenceItem(
            evidence_id=item.evidence_id,
            kind="row",
            content_hash=item.content_hash,
            source_refs=item.source_refs,
            fields=tuple(
                EvidenceField(name=field.name, value=field.value) for field in item.fields
            ),
            provenance=(EvidenceProvenance(attempt=1, modality="structured", rank=index),),
        )
        for index, item in enumerate(raw.evidence, start=1)
    )
    attempts = tuple(
        AttemptRecord(
            attempt=item.semantic_attempt,
            operation_id=item.plan_fingerprint[:32],
            kind=AttemptKind.QUERY_PLAN,
            outcome=_attempt_outcome(item.outcome),
            error_code=item.diagnostic.code if item.diagnostic else None,
        )
        for item in raw.attempts
    )
    return ToolOutcome(
        call_id=raw.call_id,
        intent_fingerprint=fingerprint,
        tool="query_records",
        status=OutcomeStatus(raw.status),
        attempts=attempts,
        evidence=evidence,
        coverage=(
            CoverageUpdate(
                dimension="query_records",
                status=_coverage_status(raw.status),
                digest=canonical_fingerprint(
                    {"tool": "query_records", "status": raw.status, "intent": fingerprint}
                ),
            ),
        ),
        warnings=raw.warnings,
        consumption=BudgetConsumption.model_validate(raw.consumption.model_dump()),
    )


def _attempt_outcome(outcome: str) -> Literal["rejected", "failed", "succeeded"]:
    if outcome == "rejected":
        return "rejected"
    return "failed" if outcome == "failed" else "succeeded"


def _coverage_status(status: str) -> str:
    if status == "query_sufficient":
        return "complete"
    if status == "no_support":
        return "miss"
    return "incomplete"


def _progress_writer(
    runtime: ToolRuntime[RuntimeContext, Any],
) -> Callable[[Mapping[str, object]], None]:
    def write(data: Mapping[str, object]) -> None:
        safe = {key: value for key, value in data.items() if key in _PROGRESS_KEYS}
        safe.setdefault("tool", "query_records")
        try:
            runtime.stream_writer(safe)
        except RuntimeError:
            return

    return write


__all__ = ["build_query_records_tool"]
