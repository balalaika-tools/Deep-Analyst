"""Trusted concurrent retrieval and deterministic reciprocal-rank fusion."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from investigation_agent.genai.evidence_search.schemas import (
    FusedCandidate,
    ModalityContribution,
    RetrievalCandidate,
    RetrievalModality,
    RetrievalQuery,
)


class TextEmbedder(Protocol):
    async def embed(self, text: str, *, deadline: float) -> Sequence[float]: ...


class CandidateReader(Protocol):
    async def search_lexical(
        self,
        *,
        case_id: str,
        query: RetrievalQuery,
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]: ...

    async def search_vector(
        self,
        *,
        case_id: str,
        query: RetrievalQuery,
        embedding: Sequence[float],
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]: ...


@dataclass(frozen=True, slots=True)
class FusionPolicy:
    lexical_weight: float = 1.0
    vector_weight: float = 1.0
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if self.lexical_weight < 0 or self.vector_weight < 0:
            raise ValueError("fusion weights cannot be negative")
        if self.lexical_weight == 0 and self.vector_weight == 0:
            raise ValueError("at least one fusion weight must be positive")
        if self.rrf_k < 1:
            raise ValueError("rrf_k must be positive")


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    candidates: tuple[FusedCandidate, ...]
    lexical_status: str
    vector_status: str
    physical_attempts: int
    warnings: tuple[str, ...]

    @property
    def partial(self) -> bool:
        return self.lexical_status != "ok" or self.vector_status != "ok"


class CandidateIntegrityError(ValueError):
    """A trusted adapter returned an internally inconsistent evidence envelope."""


async def retrieve_hybrid(
    *,
    reader: CandidateReader,
    embedder: TextEmbedder,
    case_id: str,
    query: RetrievalQuery,
    excluded_chunk_ids: frozenset[str],
    deadline: float,
    policy: FusionPolicy,
) -> HybridRetrievalResult:
    """Run both modalities concurrently and retain a safe partial result when one fails."""

    lexical_task = asyncio.create_task(
        reader.search_lexical(
            case_id=case_id,
            query=query,
            excluded_chunk_ids=excluded_chunk_ids,
            deadline=deadline,
        )
    )
    vector_task = asyncio.create_task(
        _embed_then_search(
            reader=reader,
            embedder=embedder,
            case_id=case_id,
            query=query,
            excluded_chunk_ids=excluded_chunk_ids,
            deadline=deadline,
        )
    )
    lexical_result, vector_result = await asyncio.gather(
        lexical_task,
        vector_task,
        return_exceptions=True,
    )

    lexical, lexical_status, lexical_warning = _unwrap_modality(
        lexical_result,
        modality=RetrievalModality.BM25,
        case_id=case_id,
        exclusions=excluded_chunk_ids,
    )
    vector, vector_status, vector_warning = _unwrap_modality(
        vector_result,
        modality=RetrievalModality.VECTOR,
        case_id=case_id,
        exclusions=excluded_chunk_ids,
    )
    warnings = tuple(item for item in (lexical_warning, vector_warning) if item)
    return HybridRetrievalResult(
        candidates=fuse_candidates(
            lexical=lexical,
            vector=vector,
            policy=policy,
            limit=query.top_k,
        ),
        lexical_status=lexical_status,
        vector_status=vector_status,
        # One lexical DB call plus one embedding and one vector DB call. Retry
        # wrappers report any additional physical calls at their own boundary.
        physical_attempts=3,
        warnings=warnings,
    )


async def _embed_then_search(
    *,
    reader: CandidateReader,
    embedder: TextEmbedder,
    case_id: str,
    query: RetrievalQuery,
    excluded_chunk_ids: frozenset[str],
    deadline: float,
) -> Sequence[RetrievalCandidate]:
    embedding = await embedder.embed(query.query, deadline=deadline)
    return await reader.search_vector(
        case_id=case_id,
        query=query,
        embedding=embedding,
        excluded_chunk_ids=excluded_chunk_ids,
        deadline=deadline,
    )


def _unwrap_modality(
    result: Sequence[RetrievalCandidate] | BaseException,
    *,
    modality: RetrievalModality,
    case_id: str,
    exclusions: frozenset[str],
) -> tuple[tuple[RetrievalCandidate, ...], str, str | None]:
    if isinstance(result, asyncio.CancelledError):
        raise result
    if isinstance(result, BaseException):
        return (), "failed", f"{modality.value}_unavailable"
    candidates = tuple(result)
    for candidate in candidates:
        if candidate.case_id != case_id:
            raise CandidateIntegrityError("retrieval candidate escaped the trusted case scope")
        if candidate.modality is not modality:
            raise CandidateIntegrityError("retrieval candidate has the wrong modality")
        if candidate.chunk_id in exclusions:
            raise CandidateIntegrityError("retrieval adapter returned an excluded chunk")
        if candidate.source_ref.record_id != candidate.record_id:
            raise CandidateIntegrityError("retrieval source reference does not match its record")
    return candidates, "ok", None


def fuse_candidates(
    *,
    lexical: Sequence[RetrievalCandidate],
    vector: Sequence[RetrievalCandidate],
    policy: FusionPolicy,
    limit: int,
) -> tuple[FusedCandidate, ...]:
    """Fuse rankings independent of provider return order, with stable chunk-ID ties."""

    if limit < 1:
        raise ValueError("fusion limit must be positive")
    ordered = {
        RetrievalModality.BM25: _normalize_ranking(lexical, RetrievalModality.BM25),
        RetrievalModality.VECTOR: _normalize_ranking(vector, RetrievalModality.VECTOR),
    }
    weights = {
        RetrievalModality.BM25: policy.lexical_weight,
        RetrievalModality.VECTOR: policy.vector_weight,
    }
    by_chunk: dict[str, tuple[RetrievalCandidate, list[ModalityContribution]]] = {}
    for modality in (RetrievalModality.BM25, RetrievalModality.VECTOR):
        for normalized_rank, candidate in enumerate(ordered[modality], start=1):
            contribution = ModalityContribution(
                modality=modality,
                rank=normalized_rank,
                raw_score=candidate.raw_score,
                weighted_rrf_score=weights[modality] / (policy.rrf_k + normalized_rank),
            )
            existing = by_chunk.get(candidate.chunk_id)
            if existing is None:
                by_chunk[candidate.chunk_id] = (candidate, [contribution])
                continue
            _assert_same_evidence(existing[0], candidate)
            existing[1].append(contribution)

    fused = [
        FusedCandidate(
            chunk_id=candidate.chunk_id,
            record_id=candidate.record_id,
            case_id=candidate.case_id,
            text=candidate.text,
            content_hash=candidate.content_hash,
            source_refs=(candidate.source_ref,),
            source_system=candidate.source_system,
            event_time_utc=candidate.event_time_utc,
            fused_score=sum(item.weighted_rrf_score for item in contributions),
            contributions=tuple(sorted(contributions, key=lambda item: item.modality.value)),
        )
        for candidate, contributions in by_chunk.values()
    ]
    fused.sort(key=lambda item: (-item.fused_score, item.chunk_id))
    return tuple(fused[:limit])


def _normalize_ranking(
    candidates: Sequence[RetrievalCandidate],
    modality: RetrievalModality,
) -> tuple[RetrievalCandidate, ...]:
    best: dict[str, RetrievalCandidate] = {}
    for candidate in candidates:
        if candidate.modality is not modality:
            raise CandidateIntegrityError("candidate appeared in an incorrect modality ranking")
        incumbent = best.get(candidate.chunk_id)
        if incumbent is None or (candidate.rank, candidate.chunk_id) < (
            incumbent.rank,
            incumbent.chunk_id,
        ):
            best[candidate.chunk_id] = candidate
    return tuple(sorted(best.values(), key=lambda item: (item.rank, item.chunk_id)))


def _assert_same_evidence(left: RetrievalCandidate, right: RetrievalCandidate) -> None:
    if (
        left.record_id,
        left.case_id,
        left.text,
        left.content_hash,
        left.source_ref,
    ) != (
        right.record_id,
        right.case_id,
        right.text,
        right.content_hash,
        right.source_ref,
    ):
        raise CandidateIntegrityError("modalities disagree about the same chunk")


__all__ = [
    "CandidateIntegrityError",
    "CandidateReader",
    "FusionPolicy",
    "HybridRetrievalResult",
    "TextEmbedder",
    "fuse_candidates",
    "retrieve_hybrid",
]
