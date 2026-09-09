"""The evidence-search tool and its agent-artifact translation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from investigation_agent.core.context import RuntimeContext
from investigation_agent.domain.investigation_state import parse_state
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
from investigation_agent.genai.evidence_search.agent import SearchEvidenceAgent
from investigation_agent.genai.evidence_search.schemas import SearchIntent, SearchOutcome
from investigation_agent.genai.shared.retry import retry_deadline

_PROGRESS_KEYS = frozenset({"phase", "tool", "attempt", "count"})


def build_search_evidence_tool(search: SearchEvidenceAgent) -> BaseTool:
    @tool(response_format="content_and_artifact")
    async def search_evidence(
        intent: SearchIntent, runtime: ToolRuntime[RuntimeContext, Any]
    ) -> tuple[str, ToolOutcome]:
        """Hybrid lexical and semantic search over the global text evidence."""

        context = runtime.context
        context.check_active()
        progress = _progress_writer(runtime)
        progress({"phase": "searching_evidence", "tool": "search_evidence", "attempt": 1})
        raw = await search.run(
            intent,
            call_id=runtime.tool_call_id or "search_evidence",
            deadline=retry_deadline(context),
            cancellation=context.cancellation,
            seen_chunk_ids=_seen_chunk_ids(runtime.state),
            progress=progress,
        )
        outcome = _to_tool_outcome(raw, intent=intent)
        return outcome.status.value, outcome

    return search_evidence


def _to_tool_outcome(raw: SearchOutcome, *, intent: SearchIntent) -> ToolOutcome:
    fingerprint = canonical_fingerprint({"tool": "search_evidence", "intent": intent})
    evidence = tuple(
        EvidenceItem(
            evidence_id=item.evidence_id,
            kind="chunk",
            content_hash=item.content_hash,
            source_refs=item.source_refs,
            content=item.content,
            evidentiary_status="verified",
            provenance=tuple(
                EvidenceProvenance(
                    attempt=part.semantic_attempt,
                    modality=part.modality.value,
                    rank=part.rank,
                    score=part.raw_score,
                )
                for part in item.provenance
            ),
        )
        for item in raw.evidence
    )
    attempts = tuple(
        AttemptRecord(
            attempt=item.semantic_attempt,
            operation_id=item.query_fingerprint[:32],
            kind=AttemptKind.RETRIEVAL,
            outcome="succeeded" if item.safe_diagnostic is None else "failed",
            error_code=item.safe_diagnostic,
        )
        for item in raw.attempts
    )
    return ToolOutcome(
        call_id=raw.call_id,
        intent_fingerprint=fingerprint,
        tool="search_evidence",
        status=OutcomeStatus(raw.status),
        attempts=attempts,
        evidence=evidence,
        coverage=(
            CoverageUpdate(
                dimension="search_evidence",
                status=_coverage_status(raw.status),
                digest=canonical_fingerprint(
                    {"tool": "search_evidence", "status": raw.status, "intent": fingerprint}
                ),
            ),
        ),
        warnings=raw.warnings,
        consumption=BudgetConsumption.model_validate(raw.consumption.model_dump()),
    )


def _coverage_status(status: str) -> str:
    if status == "sufficient":
        return "complete"
    if status == "no_retrieved_support":
        return "miss"
    return "incomplete"


def _progress_writer(
    runtime: ToolRuntime[RuntimeContext, Any],
) -> Callable[[Mapping[str, object]], None]:
    def write(data: Mapping[str, object]) -> None:
        safe = {key: value for key, value in data.items() if key in _PROGRESS_KEYS}
        safe.setdefault("tool", "search_evidence")
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


__all__ = ["build_search_evidence_tool"]
