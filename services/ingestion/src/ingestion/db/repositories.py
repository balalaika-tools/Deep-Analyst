"""Upserts on natural keys. Every write is idempotent; a re-run leaves counts unchanged."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from evidence_model import (
    AccountRow,
    ChunkRow,
    CommunicationRow,
    EntityDraft,
    EntityRow,
    IngestionRunRow,
    RecordRow,
    RelationshipDraft,
    RelationshipRow,
    TransactionRow,
)
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, col

from ingestion.domain.chunking import Chunk
from ingestion.domain.records import (
    AccountProjection,
    CommunicationProjection,
    SourceRecord,
    TransactionProjection,
)
from ingestion.ports.ingestion_ledger import RunStart


async def _upsert(
    session: AsyncSession, table: type[SQLModel], rows: Sequence[dict[str, Any]], *, key: str
) -> None:
    """INSERT ... ON CONFLICT (key) DO UPDATE with every non-key column, in chunks."""
    if not rows:
        return
    columns = [column for column in rows[0] if column != key]
    for start in range(0, len(rows), 500):
        statement = insert(table).values(rows[start : start + 500])
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[key],
                set_={column: getattr(statement.excluded, column) for column in columns},
            )
        )


def _record_row(record: SourceRecord) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "source_system": record.source_system,
        "source_record_id": record.source_record_id,
        "record_type": record.record_type,
        "event_time_utc": record.event_time_utc,
        "original_time": record.original_time,
        "text": record.text,
        "payload": record.payload,
        "source_path": record.source_path,
        "content_hash": record.content_hash,
    }


def _projection_row(projection: object, *, exclude: frozenset[str]) -> dict[str, Any]:
    return {
        name: getattr(projection, name)
        for name in projection.__slots__  # type: ignore[attr-defined]
        if name not in exclude
    }


class RecordRepository:
    """Records and their derived projections, one transaction per source."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_records(self, records: Sequence[SourceRecord]) -> None:
        await _upsert(self._session, RecordRow, [_record_row(r) for r in records], key="record_id")

    async def upsert_communications(self, rows: Sequence[CommunicationProjection]) -> None:
        exclude = frozenset({"from_field", "to_field"})
        await _upsert(
            self._session,
            CommunicationRow,
            [_projection_row(row, exclude=exclude) for row in rows],
            key="record_id",
        )

    async def upsert_accounts(self, rows: Sequence[AccountProjection]) -> None:
        await _upsert(
            self._session,
            AccountRow,
            [_projection_row(row, exclude=frozenset()) for row in rows],
            key="record_id",
        )

    async def upsert_transactions(self, rows: Sequence[TransactionProjection]) -> None:
        await _upsert(
            self._session,
            TransactionRow,
            [_projection_row(row, exclude=frozenset()) for row in rows],
            key="record_id",
        )


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_chunks(self, items: Iterable[tuple[SourceRecord, Chunk, list[float]]]) -> int:
        rows = [
            {
                "chunk_id": f"{record.record_id}#{chunk.char_start}-{chunk.char_end}",
                "record_id": record.record_id,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "text": chunk.text,
                "source_system": record.source_system,
                "event_time_utc": record.event_time_utc,
                "embedding": embedding,
            }
            for record, chunk, embedding in items
        ]
        await _upsert(self._session, ChunkRow, rows, key="chunk_id")
        return len(rows)


class GraphRepository:
    """Entities then relationships, in deterministic identifier order."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_entities(self, entities: Iterable[EntityDraft]) -> int:
        rows = [
            {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type.value,
                "label": entity.label,
                "normalized_key": entity.normalized_key,
                "source_refs": [ref.model_dump(mode="json") for ref in entity.source_refs],
            }
            for entity in sorted(entities, key=lambda e: e.entity_id)
        ]
        await _upsert(self._session, EntityRow, rows, key="entity_id")
        return len(rows)

    async def upsert_relationships(self, relationships: Iterable[RelationshipDraft]) -> int:
        rows = [
            {
                "relationship_id": edge.relationship_id,
                "subject_entity_id": edge.subject.entity_id,
                "predicate": edge.predicate.value,
                "object_entity_id": edge.object.entity_id,
                "status": edge.status.value,
                "method": edge.method.value,
                "occurred_at": edge.occurred_at,
                "valid_from": edge.valid_from,
                "valid_to": edge.valid_to,
                "source_refs": [ref.model_dump(mode="json") for ref in edge.source_refs],
                "attributes": edge.attributes,
            }
            for edge in sorted(relationships, key=lambda r: r.relationship_id)
        ]
        await _upsert(self._session, RelationshipRow, rows, key="relationship_id")
        return len(rows)


class RunLedgerRepository:
    """`RunLedger` over the `ingestion_runs` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_completed(self, fingerprint: str) -> bool:
        statement = select(func.count()).where(
            col(IngestionRunRow.fingerprint) == fingerprint,
            col(IngestionRunRow.outcome) == "completed",
        )
        return bool((await self._session.execute(statement)).scalar_one())

    async def start(self, run: RunStart) -> str:
        row = IngestionRunRow(
            run_id=_run_id(run),
            fingerprint=run.fingerprint,
            dataset_version=run.dataset_version,
            embedding_model_id=run.embedding_model_id,
            started_at=run.started_at,
            outcome="running",
            summary={},
        )
        self._session.add(row)
        await self._session.flush()
        return row.run_id

    async def complete(
        self, run_id: str, *, completed_at: datetime, summary: dict[str, Any]
    ) -> None:
        await self._session.execute(
            update(IngestionRunRow)
            .where(col(IngestionRunRow.run_id) == run_id)
            .values(outcome="completed", completed_at=completed_at, summary=summary)
        )

    async def fail(self, run_id: str, *, completed_at: datetime, error_type: str) -> None:
        await self._session.execute(
            update(IngestionRunRow)
            .where(col(IngestionRunRow.run_id) == run_id)
            .values(outcome="failed", completed_at=completed_at, summary={"error.type": error_type})
        )


def _run_id(run: RunStart) -> str:
    return uuid.uuid4().hex


async def table_counts(session: AsyncSession) -> dict[str, int]:
    """Row counts per table, used by tests and the run summary."""
    counts: dict[str, int] = {}
    for table in (
        RecordRow,
        EntityRow,
        RelationshipRow,
        TransactionRow,
        AccountRow,
        CommunicationRow,
        ChunkRow,
    ):
        counts[str(table.__tablename__)] = int(
            (await session.execute(select(func.count()).select_from(table))).scalar_one()
        )
    return counts
