"""Versioned, bounded tool outcomes accepted by the evidence-index hook."""

from __future__ import annotations

import hashlib
import json
from enum import Enum, StrEnum
from typing import Annotated, Any, Literal

from evidence_model import SourceRef
from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt


class OutcomeStatus(StrEnum):
    SUFFICIENT = "sufficient"
    NO_RETRIEVED_SUPPORT = "no_retrieved_support"
    RETRIEVAL_INCOMPLETE = "retrieval_incomplete"
    QUERY_SUFFICIENT = "query_sufficient"
    QUERY_EXHAUSTED = "query_exhausted"
    CONNECTIONS_FOUND = "connections_found"
    NO_SUPPORT = "no_support"
    POLICY_REJECTED = "policy_rejected"
    TRANSIENT_EXHAUSTED = "transient_exhausted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    VALIDATION_FAILED = "validation_failed"


class AttemptKind(StrEnum):
    MODEL = "model"
    EMBEDDING = "embedding"
    DATABASE = "database"
    RETRIEVAL = "retrieval"
    QUERY_PLAN = "query_plan"
    GRAPH = "graph"


class AttemptRecord(BaseModel):
    """Safe attempt metadata; prompts, SQL, arguments, and result content are excluded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: Annotated[int, Field(ge=1)]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)]
    kind: AttemptKind
    outcome: Literal["succeeded", "failed", "rejected", "cancelled"]
    error_code: Annotated[str, Field(min_length=1, max_length=64)] | None = None


class BudgetConsumption(BaseModel):
    """Measured resource use accounted exactly once with the owning call ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_calls: NonNegativeInt = 0
    closure_model_calls: NonNegativeInt = 0
    planner_decisions: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    physical_attempts: NonNegativeInt = 0
    semantic_attempts: NonNegativeInt = 0
    rows: NonNegativeInt = 0
    bytes: NonNegativeInt = 0
    paths: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    context_tokens: NonNegativeInt = 0
    answer_chars: NonNegativeInt = 0
    elapsed_ms: NonNegativeInt = 0

    @property
    def is_empty(self) -> bool:
        return all(value == 0 for value in self.model_dump().values())


class EvidenceField(BaseModel):
    """One bounded scalar field from a structured row or graph object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=128)]
    value: str | int | float | bool | None


class EvidenceProvenance(BaseModel):
    """How one tool attempt surfaced an evidence item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: Annotated[int, Field(ge=1)]
    modality: Literal["bm25", "vector", "structured", "graph", "derived"]
    rank: Annotated[int, Field(ge=1)] | None = None
    score: NonNegativeFloat | None = None


class EvidenceItem(BaseModel):
    """A source-preserving, model-visible item safe to persist in the bounded ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: Annotated[str, Field(min_length=1, max_length=256)]
    kind: Literal["chunk", "row", "entity", "relationship", "finding"]
    case_id: Annotated[str, Field(min_length=1, max_length=128)]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=32)]
    content: Annotated[str | None, Field(max_length=32_000)] = None
    fields: Annotated[tuple[EvidenceField, ...], Field(max_length=64)] = ()
    evidentiary_status: Literal["verified", "confirmed", "proposed"] = "verified"
    provenance: Annotated[tuple[EvidenceProvenance, ...], Field(max_length=16)] = ()
    untrusted_content: bool = True
    suspicious_content: bool = False
    guard_status: Literal["unchecked", "allowed", "flagged"] = "unchecked"


class CoverageUpdate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: Annotated[str, Field(min_length=1, max_length=128)]
    status: Literal["complete", "incomplete", "miss", "unavailable", "bounded"]
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ToolOutcome(BaseModel):
    """The sole envelope through which tool work may change durable evidence state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    call_id: Annotated[str, Field(min_length=1, max_length=128)]
    intent_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    tool: Literal["search_evidence", "query_records", "find_connections"]
    case_id: Annotated[str, Field(min_length=1, max_length=128)]
    status: OutcomeStatus
    attempts: Annotated[tuple[AttemptRecord, ...], Field(max_length=32)] = ()
    evidence: Annotated[tuple[EvidenceItem, ...], Field(max_length=256)] = ()
    coverage: Annotated[tuple[CoverageUpdate, ...], Field(max_length=64)] = ()
    warnings: Annotated[
        tuple[Annotated[str, Field(max_length=256)], ...], Field(max_length=32)
    ] = ()
    failure_code: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    consumption: BudgetConsumption = Field(default_factory=BudgetConsumption)


def _canonical_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _canonical_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_payload(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_json(value: Any) -> str:
    """Serialize supported fingerprint inputs with stable keys and no insignificant whitespace."""

    payload = _canonical_payload(value)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "AttemptKind",
    "AttemptRecord",
    "BudgetConsumption",
    "CoverageUpdate",
    "EvidenceField",
    "EvidenceItem",
    "EvidenceProvenance",
    "OutcomeStatus",
    "ToolOutcome",
    "canonical_fingerprint",
    "canonical_json",
]
