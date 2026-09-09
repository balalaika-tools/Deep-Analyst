"""Model-facing intent, verdict, and outcome schemas for record queries."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

from investigation_agent.ports.record_query import SafeDiagnostic, StructuredRowEvidence

SCHEMA_VERSION = 1
MAX_SEMANTIC_ATTEMPTS = 3
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class QueryConstraint(StrictModel):
    constraint_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")]
    description: Annotated[str, Field(min_length=1, max_length=1_000)]
    machine_enforceable: bool = True


class QueryIntent(StrictModel):
    """Main-agent-authored intent without credentials, roles, or prior SQL."""

    question: Annotated[str, Field(min_length=1, max_length=8_000)]
    objective: Annotated[str, Field(min_length=1, max_length=4_000)]
    hard_constraints: Annotated[tuple[QueryConstraint, ...], Field(max_length=32)] = ()
    soft_constraints: Annotated[tuple[QueryConstraint, ...], Field(max_length=32)] = ()
    selected_evidence_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=64)
    ] = ()
    desired_result_shape: Annotated[str, Field(min_length=1, max_length=1_000)]

    @model_validator(mode="after")
    def _identifiers_are_unique(self) -> QueryIntent:
        identifiers = [
            item.constraint_id for item in (*self.hard_constraints, *self.soft_constraints)
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("constraint identifiers must be unique")
        return self


class QueryVerdict(StrictModel):
    """The nested agent's structured response; trusted code validates every row identifier."""

    status: Literal["query_sufficient", "query_exhausted"]
    selected_row_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=200)
    ] = ()
    safe_reason_code: Literal[
        "sufficient",
        "insufficient",
        "conflicting",
        "attempts_exhausted",
    ]


class QueryAttempt(StrictModel):
    semantic_attempt: Annotated[int, Field(ge=1, le=MAX_SEMANTIC_ATTEMPTS)]
    plan_fingerprint: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    physical_attempts: Annotated[int, Field(ge=0, le=12)] = 0
    outcome: Literal["ok", "empty", "rejected", "failed"]
    diagnostic: SafeDiagnostic | None = None
    row_count: NonNegativeInt = 0


class QueryConsumption(StrictModel):
    model_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    rows: NonNegativeInt = 0
    bytes: NonNegativeInt = 0
    physical_attempts: NonNegativeInt = 0


class QueryOutcome(StrictModel):
    schema_version: Literal[1] = 1
    call_id: Annotated[str, Field(min_length=1, max_length=128)]
    intent_fingerprint: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    status: Literal["query_sufficient", "query_exhausted"]
    attempts: Annotated[tuple[QueryAttempt, ...], Field(max_length=MAX_SEMANTIC_ATTEMPTS)] = ()
    evidence: Annotated[tuple[StructuredRowEvidence, ...], Field(max_length=1_000)] = ()
    warnings: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=16)
    ] = ()
    consumption: QueryConsumption = Field(default_factory=QueryConsumption)


__all__ = [
    "MAX_SEMANTIC_ATTEMPTS",
    "QueryAttempt",
    "QueryConstraint",
    "QueryConsumption",
    "QueryIntent",
    "QueryOutcome",
    "QueryVerdict",
    "SCHEMA_VERSION",
]
