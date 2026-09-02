from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from evidence_model import SourceRef, TextSpanLocator
from investigation_agent.genai.evidence_search.retrieval import (
    FusionPolicy,
    fuse_candidates,
    retrieve_hybrid,
)
from investigation_agent.genai.evidence_search.schemas import (
    RetrievalCandidate,
    RetrievalModality,
    RetrievalQuery,
)

HASH = "a" * 64


def candidate(
    chunk_id: str,
    modality: RetrievalModality,
    *,
    rank: int,
    score: float,
) -> RetrievalCandidate:
    text = f"evidence {chunk_id}"
    return RetrievalCandidate(
        chunk_id=chunk_id,
        record_id=f"record:{chunk_id}",
        text=text,
        content_hash=HASH,
        source_ref=SourceRef(
            record_id=f"record:{chunk_id}",
            locator=TextSpanLocator(
                field="text",
                char_start=0,
                char_end=len(text),
                quote=text,
            ),
        ),
        source_system="documents",
        modality=modality,
        raw_score=score,
        rank=rank,
    )


def test_weighted_rrf_is_order_independent_and_preserves_both_modalities() -> None:
    lexical = [
        candidate("c-1", RetrievalModality.BM25, rank=1, score=4.0),
        candidate("c-2", RetrievalModality.BM25, rank=2, score=3.0),
    ]
    vector = [
        candidate("c-2", RetrievalModality.VECTOR, rank=1, score=0.9),
        candidate("c-3", RetrievalModality.VECTOR, rank=2, score=0.8),
    ]
    policy = FusionPolicy(lexical_weight=0.6, vector_weight=0.4, rrf_k=10)

    first = fuse_candidates(lexical=lexical, vector=vector, policy=policy, limit=10)
    reordered = fuse_candidates(
        lexical=list(reversed(lexical)),
        vector=list(reversed(vector)),
        policy=policy,
        limit=10,
    )

    assert first == reordered
    assert [item.chunk_id for item in first] == ["c-2", "c-1", "c-3"]
    overlap = next(item for item in first if item.chunk_id == "c-2")
    assert [item.modality for item in overlap.contributions] == [
        RetrievalModality.BM25,
        RetrievalModality.VECTOR,
    ]


class _Embedder:
    async def embed(self, text: str, *, deadline: float) -> Sequence[float]:
        del text, deadline
        return [0.1, 0.2]


class _PartialReader:
    def __init__(self) -> None:
        self.exclusions: list[frozenset[str]] = []

    async def search_lexical(
        self,
        *,
        query: RetrievalQuery,
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]:
        del query, deadline
        self.exclusions.append(excluded_chunk_ids)
        item = candidate("c-1", RetrievalModality.BM25, rank=1, score=3.0)
        return [item]

    async def search_vector(
        self,
        *,
        query: RetrievalQuery,
        embedding: Sequence[float],
        excluded_chunk_ids: frozenset[str],
        deadline: float,
    ) -> Sequence[RetrievalCandidate]:
        del query, embedding, excluded_chunk_ids, deadline
        raise ConnectionError("provider detail must not escape")


@pytest.mark.asyncio
async def test_modality_failure_returns_a_safe_partial_batch() -> None:
    reader = _PartialReader()

    result = await retrieve_hybrid(
        reader=reader,
        embedder=_Embedder(),
        query=RetrievalQuery(query="invoice reference"),
        excluded_chunk_ids=frozenset({"old"}),
        deadline=asyncio.get_running_loop().time() + 5,
        policy=FusionPolicy(),
    )

    assert [item.chunk_id for item in result.candidates] == ["c-1"]
    assert result.lexical_status == "ok"
    assert result.vector_status == "failed"
    assert result.warnings == ("vector_unavailable",)
    assert reader.exclusions == [frozenset({"old"})]
