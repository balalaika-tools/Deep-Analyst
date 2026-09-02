"""Bounded contracts for the hybrid evidence-search nested agent."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from evidence_model import SourceRef
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

SCHEMA_VERSION = 1
MAX_SEMANTIC_ATTEMPTS = 3
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

type SearchStatus = Literal["sufficient", "no_retrieved_support", "retrieval_incomplete"]


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Constraint(StrictModel):
    constraint_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}$")]
    description: Annotated[str, Field(min_length=1, max_length=1_000)]


class SearchIntent(StrictModel):
    """Main-agent-authored intent. Exclusions come from trusted code."""

    question: Annotated[str, Field(min_length=1, max_length=8_000)]
    objective: Annotated[str, Field(min_length=1, max_length=4_000)]
    hard_constraints: Annotated[tuple[Constraint, ...], Field(max_length=32)] = ()
    soft_constraints: Annotated[tuple[Constraint, ...], Field(max_length=32)] = ()
    selected_evidence_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=64)
    ] = ()

    @model_validator(mode="after")
    def _stable_identifiers_are_unique(self) -> SearchIntent:
        constraint_ids = [
            item.constraint_id for item in (*self.hard_constraints, *self.soft_constraints)
        ]
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("constraint identifiers must be unique")
        return self


class RetrievalQuery(StrictModel):
    """One ``retrieve`` proposal; the nested agent cannot express exclusions or scope."""

    query: Annotated[str, Field(min_length=1, max_length=2_000)]
    source_systems: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=64)], ...], Field(max_length=16)
    ] = ()
    event_time_from: datetime | None = None
    event_time_to: datetime | None = None
    top_k: Annotated[int, Field(ge=1, le=100)] = 20

    @model_validator(mode="after")
    def _valid_time_window(self) -> RetrievalQuery:
        if self.event_time_from and self.event_time_from.tzinfo is None:
            raise ValueError("event_time_from must be timezone-aware")
        if self.event_time_to and self.event_time_to.tzinfo is None:
            raise ValueError("event_time_to must be timezone-aware")
        if self.event_time_from and self.event_time_to:
            if self.event_time_from > self.event_time_to:
                raise ValueError("event_time_from cannot follow event_time_to")
        return self

    def fingerprint(self) -> str:
        normalized_query = re.sub(r"\s+", " ", self.query).strip().casefold()
        payload = {
            "query": normalized_query,
            "source_systems": sorted(set(self.source_systems)),
            "event_time_from": self.event_time_from.isoformat() if self.event_time_from else None,
            "event_time_to": self.event_time_to.isoformat() if self.event_time_to else None,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class RetrievalModality(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"


class RetrievalCandidate(StrictModel):
    chunk_id: Annotated[str, Field(min_length=1, max_length=256)]
    record_id: Annotated[str, Field(min_length=1, max_length=256)]
    text: Annotated[str, Field(max_length=32_000)]
    content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_ref: SourceRef
    source_system: Annotated[str, Field(min_length=1, max_length=64)]
    event_time_utc: datetime | None = None
    modality: RetrievalModality
    raw_score: float
    rank: Annotated[int, Field(ge=1, le=10_000)]


class ModalityContribution(StrictModel):
    semantic_attempt: Annotated[int, Field(ge=1, le=MAX_SEMANTIC_ATTEMPTS)] = 1
    modality: RetrievalModality
    rank: Annotated[int, Field(ge=1, le=10_000)]
    raw_score: float
    weighted_rrf_score: Annotated[float, Field(ge=0)]


class FusedCandidate(StrictModel):
    chunk_id: Annotated[str, Field(min_length=1, max_length=256)]
    record_id: Annotated[str, Field(min_length=1, max_length=256)]
    text: Annotated[str, Field(max_length=32_000)]
    content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=4)]
    source_system: Annotated[str, Field(min_length=1, max_length=64)]
    event_time_utc: datetime | None = None
    fused_score: Annotated[float, Field(ge=0)]
    contributions: Annotated[tuple[ModalityContribution, ...], Field(min_length=1, max_length=2)]


class SearchVerdict(StrictModel):
    """The nested agent's structured response; trusted code validates every identifier."""

    status: SearchStatus
    selected_chunk_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=100)
    ] = ()
    safe_reason_code: Literal[
        "sufficient",
        "irrelevant",
        "partial_coverage",
        "conflicting_support",
        "attempts_exhausted",
    ]


class SearchAttempt(StrictModel):
    semantic_attempt: Annotated[int, Field(ge=1, le=MAX_SEMANTIC_ATTEMPTS)]
    query_fingerprint: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    physical_attempts: Annotated[int, Field(ge=0, le=12)] = 0
    retrieved_chunk_ids: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=200)
    ] = ()
    lexical_status: Literal["ok", "failed", "not_run"] = "not_run"
    vector_status: Literal["ok", "failed", "not_run"] = "not_run"
    safe_diagnostic: Annotated[str | None, Field(max_length=128)] = None


class SearchEvidence(StrictModel):
    evidence_id: Annotated[str, Field(min_length=1, max_length=256)]
    content_hash: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    source_refs: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=4)]
    kind: Literal["chunk"] = "chunk"
    content: Annotated[str, Field(max_length=32_000)]
    evidentiary_status: Literal["verified"] = "verified"
    provenance: Annotated[tuple[ModalityContribution, ...], Field(min_length=1, max_length=2)]


class SearchConsumption(StrictModel):
    model_calls: NonNegativeInt = 0
    tool_calls: NonNegativeInt = 0
    rows: NonNegativeInt = 0
    bytes: NonNegativeInt = 0
    physical_attempts: NonNegativeInt = 0


class SearchOutcome(StrictModel):
    """Trusted tool-level outcome built from invocation-local retrievals and the verdict."""

    schema_version: Literal[1] = 1
    call_id: Annotated[str, Field(min_length=1, max_length=128)]
    intent_fingerprint: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    status: SearchStatus
    attempts: Annotated[tuple[SearchAttempt, ...], Field(max_length=MAX_SEMANTIC_ATTEMPTS)] = ()
    evidence: Annotated[tuple[SearchEvidence, ...], Field(max_length=100)] = ()
    warnings: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=256)], ...], Field(max_length=16)
    ] = ()
    consumption: SearchConsumption = Field(default_factory=SearchConsumption)


__all__ = [
    "Constraint",
    "FusedCandidate",
    "MAX_SEMANTIC_ATTEMPTS",
    "ModalityContribution",
    "RetrievalCandidate",
    "RetrievalModality",
    "RetrievalQuery",
    "SCHEMA_VERSION",
    "SearchAttempt",
    "SearchConsumption",
    "SearchEvidence",
    "SearchIntent",
    "SearchOutcome",
    "SearchStatus",
    "SearchVerdict",
]
