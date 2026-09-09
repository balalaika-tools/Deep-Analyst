"""Contracts for lexical and vector evidence retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol

from evidence_model import SourceRef
from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RetrievalQuery(_StrictModel):
    """One bounded retrieval request over the trusted evidence store."""

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


class RetrievalCandidate(_StrictModel):
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


class EvidenceCandidateReader(Protocol):
    async def search_lexical(
        self,
        *,
        query: RetrievalQuery,
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]: ...

    async def search_vector(
        self,
        *,
        query: RetrievalQuery,
        embedding: Sequence[float],
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]: ...


__all__ = [
    "EvidenceCandidateReader",
    "RetrievalCandidate",
    "RetrievalModality",
    "RetrievalQuery",
]
