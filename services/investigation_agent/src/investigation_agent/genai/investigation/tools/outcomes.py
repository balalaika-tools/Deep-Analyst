"""Normalize capability-specific results into the main agent's tool contract."""

from __future__ import annotations

from typing import Literal

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
from investigation_agent.genai.evidence_search.schemas import SearchIntent, SearchOutcome
from investigation_agent.genai.investigation.connections import (
    FindConnectionsInput,
    FindConnectionsOutcome,
)
from investigation_agent.genai.record_query.schemas import QueryIntent, QueryOutcome


def search_outcome(raw: SearchOutcome, *, intent: SearchIntent) -> ToolOutcome:
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
        coverage=(_coverage("search_evidence", raw.status, fingerprint),),
        warnings=raw.warnings,
        consumption=BudgetConsumption.model_validate(raw.consumption.model_dump()),
    )


def query_outcome(raw: QueryOutcome, *, intent: QueryIntent) -> ToolOutcome:
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
            outcome=_query_attempt_outcome(item.outcome),
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
        coverage=(_coverage("query_records", raw.status, fingerprint),),
        warnings=raw.warnings,
        consumption=BudgetConsumption.model_validate(raw.consumption.model_dump()),
    )


def _query_attempt_outcome(outcome: str) -> Literal["rejected", "failed", "succeeded"]:
    if outcome == "rejected":
        return "rejected"
    return "failed" if outcome == "failed" else "succeeded"


def connections_outcome(
    raw: FindConnectionsOutcome,
    *,
    request: FindConnectionsInput,
    physical_attempts: int = 1,
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
        coverage=(_coverage("find_connections", raw.status, fingerprint),),
        warnings=raw.warnings,
        consumption=consumption.model_copy(
            update={"physical_attempts": consumption.physical_attempts + physical_attempts - 1}
        ),
    )


def _coverage(tool: str, status: str, fingerprint: str) -> CoverageUpdate:
    if status in {"sufficient", "query_sufficient", "connections_found"}:
        coverage_status = "complete"
    elif status in {"no_retrieved_support", "no_support"}:
        coverage_status = "miss"
    else:
        coverage_status = "incomplete"
    return CoverageUpdate(
        dimension=tool,
        status=coverage_status,
        digest=canonical_fingerprint({"tool": tool, "status": status, "intent": fingerprint}),
    )


__all__ = ["connections_outcome", "query_outcome", "search_outcome"]
