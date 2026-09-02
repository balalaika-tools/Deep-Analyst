from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from evidence_model import (
    EndpointRef,
    EntityDraft,
    EntityType,
    ExtractionMethod,
    FieldLocator,
    Predicate,
    RelationshipDraft,
    RelationshipStatus,
    SourceRef,
)
from ingestion.adapters.fixtures import cdr, documents
from ingestion.db.engine import build_session_factory
from ingestion.db.repositories import table_counts
from ingestion.db.store import SqlEvidenceStore
from ingestion.domain.chunking import Chunk
from ingestion.ports.ingestion_ledger import RunStart
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

CASE = "case_trg_001"


def _graph() -> tuple[list[EntityDraft], list[RelationshipDraft]]:
    ref = SourceRef(record_id="cdr:c01", locator=FieldLocator(field="calling_msisdn"))
    a = EntityDraft(
        case_id=CASE,
        entity_type=EntityType.PHONE,
        label="a",
        normalized_key="306971234567",
        source_refs=[ref],
    )
    b = EntityDraft(
        case_id=CASE,
        entity_type=EntityType.PHONE,
        label="b",
        normalized_key="306949876543",
        source_refs=[ref],
    )
    edge = RelationshipDraft(
        case_id=CASE,
        subject=EndpointRef(entity_id=a.entity_id, entity_type=EntityType.PHONE),
        predicate=Predicate.COMMUNICATED_WITH,
        object=EndpointRef(entity_id=b.entity_id, entity_type=EntityType.PHONE),
        status=RelationshipStatus.CONFIRMED,
        method=ExtractionMethod.DETERMINISTIC,
        source_record_id="cdr:c01",
        source_refs=[ref],
    )
    return [a, b], [edge]


async def _persist_everything(store: SqlEvidenceStore, edition_dir: Path) -> None:
    await store.persist_source(cdr.load_cdr(edition_dir, CASE))
    docs = documents.load_documents(edition_dir, CASE)
    await store.persist_source(docs)
    items: list[Any] = [
        (record, Chunk(0, len(record.text or ""), record.text or ""), [0.1, 0.2, 0.3, 0.4])
        for record in docs.records
    ]
    await store.persist_chunks(items)
    await store.persist_graph(*_graph())


@pytest.mark.asyncio
async def test_running_the_same_batch_twice_leaves_identical_counts(
    engine: AsyncEngine, edition_dir: Path
) -> None:
    store = SqlEvidenceStore(build_session_factory(engine))
    sessions = build_session_factory(engine)

    await _persist_everything(store, edition_dir)
    async with sessions() as session:
        first = await table_counts(session)
    await _persist_everything(store, edition_dir)
    async with sessions() as session:
        second = await table_counts(session)

    assert first == second
    assert first["records"] == 65 and first["communications"] == 55 and first["chunks"] == 10
    assert first["entities"] == 2 and first["relationships"] == 1


@pytest.mark.asyncio
async def test_bm25_query_for_the_invoice_reference_ranks_exact_matches_first(
    engine: AsyncEngine, edition_dir: Path
) -> None:
    store = SqlEvidenceStore(build_session_factory(engine))
    await _persist_everything(store, edition_dir)

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT record_id, paradedb.score(chunk_id) AS score FROM chunks "
                "WHERE text @@@ paradedb.match('text', :query, conjunction_mode => true) "
                "ORDER BY score DESC"
            ),
            {"query": "INV-2231"},
        )
        hits = [row[0] for row in rows.all()]
    assert hits == ["docs:R-05"]
    assert "docs:N-D3" not in hits


@pytest.mark.asyncio
async def test_run_ledger_round_trip(engine: AsyncEngine) -> None:
    store = SqlEvidenceStore(build_session_factory(engine))
    start = RunStart(CASE, "fp-1", "v1", "embed", datetime(2026, 9, 2, tzinfo=UTC))

    assert not await store.has_completed(CASE, "fp-1")
    run_id = await store.start(start)
    await store.fail(run_id, completed_at=datetime.now(UTC), error_type="RuntimeError")
    assert not await store.has_completed(CASE, "fp-1")
    run_id = await store.start(start)
    await store.complete(run_id, completed_at=datetime.now(UTC), summary={"records": 1})
    assert await store.has_completed(CASE, "fp-1")
