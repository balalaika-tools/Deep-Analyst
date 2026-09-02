"""`EvidenceStore` and `RunLedger` over the pooled session factory."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from evidence_model import EntityDraft, RelationshipDraft
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ingestion.db.repositories import (
    ChunkRepository,
    GraphRepository,
    RecordRepository,
    RunLedgerRepository,
)
from ingestion.domain.records import SourceBatch
from ingestion.ports.evidence_store import ChunkItem
from ingestion.ports.ingestion_ledger import RunStart


class SqlEvidenceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def persist_source(self, batch: SourceBatch) -> None:
        async with self._sessions() as session, session.begin():
            records = RecordRepository(session)
            await records.upsert_records(batch.records)
            await records.upsert_communications(batch.communications)
            await records.upsert_accounts(batch.accounts)
            await records.upsert_transactions(batch.transactions)

    async def persist_chunks(self, items: Sequence[ChunkItem]) -> int:
        async with self._sessions() as session, session.begin():
            return await ChunkRepository(session).upsert_chunks(items)

    async def persist_graph(
        self, entities: Iterable[EntityDraft], relationships: Iterable[RelationshipDraft]
    ) -> tuple[int, int]:
        async with self._sessions() as session, session.begin():
            graph = GraphRepository(session)
            entity_count = await graph.upsert_entities(entities)
            relationship_count = await graph.upsert_relationships(relationships)
            return entity_count, relationship_count

    async def has_completed(self, fingerprint: str) -> bool:
        async with self._sessions() as session:
            return await RunLedgerRepository(session).has_completed(fingerprint)

    async def start(self, run: RunStart) -> str:
        async with self._sessions() as session, session.begin():
            return await RunLedgerRepository(session).start(run)

    async def complete(
        self, run_id: str, *, completed_at: datetime, summary: dict[str, Any]
    ) -> None:
        async with self._sessions() as session, session.begin():
            await RunLedgerRepository(session).complete(
                run_id, completed_at=completed_at, summary=summary
            )

    async def fail(self, run_id: str, *, completed_at: datetime, error_type: str) -> None:
        async with self._sessions() as session, session.begin():
            await RunLedgerRepository(session).fail(
                run_id, completed_at=completed_at, error_type=error_type
            )
