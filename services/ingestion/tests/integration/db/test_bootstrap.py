import pytest
from ingestion.config.settings import Settings
from ingestion.db.engine import build_engine
from ingestion.db.indexes import BM25_INDEX, HNSW_INDEX, bootstrap_store
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool


@pytest.mark.asyncio
async def test_bootstrap_creates_both_text_indexes_and_the_configured_vector_width(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as conn:
        indexes = await conn.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks'")
        )
        definitions: dict[str, str] = {
            str(name): str(definition) for name, definition in indexes.all()
        }
        width = await conn.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )

    assert "USING bm25" in definitions[BM25_INDEX]
    assert (
        "USING hnsw" in definitions[HNSW_INDEX] and "vector_cosine_ops" in definitions[HNSW_INDEX]
    )
    assert width.scalar_one() == "vector(4)"


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_changing_the_width_rebuilds_the_vector_column(
    engine: AsyncEngine,
) -> None:
    async with engine.begin() as conn:
        await bootstrap_store(conn, embedding_dimensions=4)
        await bootstrap_store(conn, embedding_dimensions=8)
        width = await conn.execute(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )
        count = await conn.execute(
            text("SELECT count(*) FROM pg_indexes WHERE indexname = :name"), {"name": HNSW_INDEX}
        )
    assert width.scalar_one() == "vector(8)"
    assert count.scalar_one() == 1


@pytest.mark.asyncio
async def test_engine_reports_the_configured_pool(test_database_url: str) -> None:
    settings = Settings(
        ENVIRONMENT_NAME="local",
        DATABASE_URL=test_database_url,
        EVIDENCE_S3_ENDPOINT="http://127.0.0.1:9090",
        EVIDENCE_S3_BUCKET="evidence-test",
        EVIDENCE_S3_ACCESS_KEY="evidence-user",
        EVIDENCE_S3_SECRET_KEY="evidence-secret",
        DATASET_EDITION="en",
        AWS_REGION="eu-central-1",
        BEDROCK_CHAT_MODEL_ID="m",
        BEDROCK_EMBEDDING_MODEL_ID="e",
        DB_POOL_SIZE=3,
        DB_MAX_OVERFLOW=2,
    )
    engine = build_engine(settings)
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
        pool = engine.pool
        assert isinstance(pool, AsyncAdaptedQueuePool)
        assert pool.size() == 3
        assert pool.__dict__["_max_overflow"] == 2
    finally:
        await engine.dispose()
    assert isinstance(create_async_engine(test_database_url), AsyncEngine)
