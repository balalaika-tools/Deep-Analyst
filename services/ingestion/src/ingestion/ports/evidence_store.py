"""Persistence contract of the evidence store, one unit of work per method."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from evidence_model import EntityDraft, RelationshipDraft

from ingestion.domain.chunking import Chunk
from ingestion.domain.records import SourceBatch, SourceRecord

type ChunkItem = tuple[SourceRecord, Chunk, list[float]]


class EvidenceStore(Protocol):
    async def persist_source(self, batch: SourceBatch) -> None: ...

    async def persist_chunks(self, items: Sequence[ChunkItem]) -> int: ...

    async def persist_graph(
        self, entities: Iterable[EntityDraft], relationships: Iterable[RelationshipDraft]
    ) -> tuple[int, int]: ...
