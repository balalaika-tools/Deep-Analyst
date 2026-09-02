"""Index DDL SQLModel cannot express: the embedding width, BM25, and HNSW."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

BM25_INDEX = "chunks_text_bm25"
HNSW_INDEX = "chunks_embedding_hnsw"


async def _current_embedding_dimensions(conn: AsyncConnection) -> int | None:
    row = await conn.execute(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        )
    )
    typmod = row.scalar_one()
    return None if typmod is None or typmod < 0 else int(typmod)


async def ensure_indexes(conn: AsyncConnection, *, embedding_dimensions: int) -> None:
    """Fix the vector width, then build the lexical and vector indexes idempotently.

    Changing the configured width drops existing embeddings: they belong to a
    different model and the fingerprint forces a full re-run anyway.
    """
    current = await _current_embedding_dimensions(conn)
    if current != embedding_dimensions:
        await conn.execute(text(f"DROP INDEX IF EXISTS {HNSW_INDEX}"))
        await conn.execute(text("UPDATE chunks SET embedding = NULL"))
        await conn.execute(
            text(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({embedding_dimensions})")
        )
    await conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {BM25_INDEX} ON chunks "
            "USING bm25 (chunk_id, text, case_id, source_system, record_id) "
            "WITH (key_field = 'chunk_id')"
        )
    )
    await conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {HNSW_INDEX} ON chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )
    )


async def bootstrap_store(conn: AsyncConnection, *, embedding_dimensions: int) -> None:
    """Extensions, tables, then indexes, in one connection."""
    from ingestion.db.extensions import create_schema, ensure_extensions

    await ensure_extensions(conn)
    await create_schema(conn)
    await ensure_indexes(conn, embedding_dimensions=embedding_dimensions)
