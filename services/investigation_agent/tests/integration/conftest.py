"""Disposable PostgreSQL lifecycle for the investigation-agent role tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
import pytest_asyncio
from investigation_agent.adapters.postgres.initializer import initialize_database
from investigation_agent.adapters.postgres.pools import (
    DatabasePools,
    PoolBounds,
    create_database_pools,
)
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

INTEGRATION_ROOT = Path(__file__).resolve().parent
READER_PASSWORD = "reader-test-password"
WRITER_PASSWORD = "writer-test-password"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(INTEGRATION_ROOT):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def owner_dsn() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("TEST_DATABASE_URL must point at a disposable ParadeDB test database")
    native = value.replace("postgresql+psycopg://", "postgresql://", 1)
    database = urlsplit(native).path.removeprefix("/")
    if "test" not in database:
        pytest.fail("TEST_DATABASE_URL must name a database containing 'test'")
    return native


async def _reset_and_initialize(owner_dsn: str) -> None:
    connection = await psycopg.AsyncConnection.connect(
        owner_dsn, autocommit=True, row_factory=dict_row
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("DROP SCHEMA IF EXISTS agent_read CASCADE")
            await cursor.execute("DROP SCHEMA IF EXISTS agent_runtime CASCADE")
            for table in (
                "relationships",
                "entities",
                "chunks",
                "communications",
                "accounts",
                "transactions",
                "records",
            ):
                await cursor.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
            await cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
            await _create_evidence_objects(cursor)
            await _seed_evidence(cursor)
    finally:
        await connection.close()

    first = await initialize_database(
        owner_dsn=owner_dsn,
        reader_password=READER_PASSWORD,
        writer_password=WRITER_PASSWORD,
        expected_version="agent-runtime@1",
    )
    second = await initialize_database(
        owner_dsn=owner_dsn,
        reader_password=READER_PASSWORD,
        writer_password=WRITER_PASSWORD,
        expected_version="agent-runtime@1",
    )
    assert first.changed is True
    assert second.changed is False


@pytest.fixture(scope="session")
def initialized_database(owner_dsn: str) -> tuple[str, str, str]:
    """Session setup runs on its own loop so function-scoped async tests keep theirs."""

    asyncio.run(_reset_and_initialize(owner_dsn))
    return (
        owner_dsn,
        _role_dsn(owner_dsn, "agent_reader", READER_PASSWORD),
        _role_dsn(owner_dsn, "agent_writer", WRITER_PASSWORD),
    )


@pytest_asyncio.fixture
async def database_pools(
    initialized_database: tuple[str, str, str],
) -> AsyncIterator[DatabasePools]:
    _owner, reader_dsn, writer_dsn = initialized_database
    bounds = PoolBounds(min_size=1, max_size=1, acquisition_timeout_s=3)
    pools = create_database_pools(
        reader_dsn=reader_dsn,
        writer_dsn=writer_dsn,
        reader_bounds=bounds,
        writer_bounds=bounds,
    )
    await pools.open()
    try:
        yield pools
    finally:
        await pools.close()


async def _create_evidence_objects(cursor: psycopg.AsyncCursor[dict[str, object]]) -> None:
    statements = (
        """CREATE TABLE public.records (
            record_id text PRIMARY KEY, case_id text NOT NULL, source_system text NOT NULL,
            source_record_id text NOT NULL, record_type text NOT NULL,
            event_time_utc timestamptz, original_time text, text text, payload jsonb NOT NULL,
            source_path text NOT NULL, content_hash text NOT NULL
        )""",
        """CREATE TABLE public.transactions (
            record_id text PRIMARY KEY REFERENCES public.records(record_id), case_id text NOT NULL,
            txn_id text NOT NULL, booking_ts_utc timestamptz NOT NULL, value_date date,
            debtor_iban text, debtor_name text, creditor_iban text, creditor_name text,
            amount_minor bigint NOT NULL, amount_text text NOT NULL, currency text NOT NULL,
            status text, remittance_info text
        )""",
        """CREATE TABLE public.accounts (
            record_id text PRIMARY KEY REFERENCES public.records(record_id), case_id text NOT NULL,
            account_id text NOT NULL, iban text, holder_name text, holder_type text, bic text,
            opened_date date
        )""",
        """CREATE TABLE public.communications (
            record_id text PRIMARY KEY REFERENCES public.records(record_id), case_id text NOT NULL,
            channel text NOT NULL, direction text NOT NULL, from_endpoint text, to_endpoint text,
            event_time_utc timestamptz NOT NULL, original_time text, duration_s integer,
            device_id text
        )""",
        """CREATE TABLE public.chunks (
            chunk_id text PRIMARY KEY, record_id text NOT NULL REFERENCES public.records(record_id),
            case_id text NOT NULL, char_start integer NOT NULL, char_end integer NOT NULL,
            text text NOT NULL, source_system text NOT NULL, event_time_utc timestamptz,
            embedding vector(4)
        )""",
        """CREATE TABLE public.entities (
            entity_id text PRIMARY KEY, case_id text NOT NULL, entity_type text NOT NULL,
            label text NOT NULL, normalized_key text, source_refs jsonb NOT NULL
        )""",
        """CREATE TABLE public.relationships (
            relationship_id text PRIMARY KEY, case_id text NOT NULL,
            subject_entity_id text NOT NULL REFERENCES public.entities(entity_id), predicate text NOT NULL,
            object_entity_id text NOT NULL REFERENCES public.entities(entity_id), status text NOT NULL,
            method text NOT NULL, occurred_at timestamptz, valid_from timestamptz,
            valid_to timestamptz, source_refs jsonb NOT NULL, attributes jsonb NOT NULL
        )""",
        """CREATE INDEX chunks_text_bm25 ON public.chunks
            USING bm25 (chunk_id, text, case_id, source_system, record_id)
            WITH (key_field = 'chunk_id')""",
        """CREATE INDEX chunks_embedding_hnsw ON public.chunks
            USING hnsw (embedding vector_cosine_ops)""",
    )
    for statement in statements:
        await cursor.execute(statement)


async def _seed_evidence(cursor: psycopg.AsyncCursor[dict[str, object]]) -> None:
    records = (
        (
            "bank:a",
            "case-a",
            "alpha invoice transfer",
            {"amount_minor": 500, "label": "Alice"},
            "a" * 64,
        ),
        (
            "bank:b",
            "case-b",
            "alpha confidential transfer",
            {"amount_minor": 900, "label": "Bob"},
            "b" * 64,
        ),
    )
    for record_id, case_id, text, payload, content_hash in records:
        await cursor.execute(
            "INSERT INTO public.records VALUES "
            "(%s, %s, 'bank', %s, 'transaction', now(), NULL, %s, %s, %s, %s)",
            (
                record_id,
                case_id,
                record_id,
                text,
                Jsonb(payload),
                f"/{record_id}",
                content_hash,
            ),
        )
        await cursor.execute(
            "INSERT INTO public.transactions VALUES "
            "(%s, %s, %s, now(), NULL, 'D', 'Debtor', 'C', 'Creditor', %s, %s, 'EUR', "
            "'booked', 'invoice')",
            (record_id, case_id, record_id, payload["amount_minor"], str(payload["amount_minor"])),
        )
        await cursor.execute(
            "INSERT INTO public.chunks VALUES (%s, %s, %s, 0, %s, %s, 'bank', now(), %s::vector)",
            (f"chunk:{case_id}", record_id, case_id, len(text), text, "[1,0,0,0]"),
        )
        reference = [
            {
                "record_id": record_id,
                "locator": {"kind": "field", "field": "label"},
            }
        ]
        for suffix, label in (("1", "First"), ("2", "Second")):
            await cursor.execute(
                "INSERT INTO public.entities VALUES (%s, %s, 'PERSON', %s, NULL, %s)",
                (f"{case_id}:entity:{suffix}", case_id, label, Jsonb(reference)),
            )
        await cursor.execute(
            "INSERT INTO public.relationships VALUES "
            "(%s, %s, %s, 'KIN_OF', %s, 'confirmed', 'deterministic', now(), NULL, NULL, %s, '{}')",
            (
                f"{case_id}:edge",
                case_id,
                f"{case_id}:entity:1",
                f"{case_id}:entity:2",
                Jsonb(reference),
            ),
        )


def _role_dsn(owner_dsn: str, role: str, password: str) -> str:
    parsed = urlsplit(owner_dsn)
    hostname = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{quote(role)}:{quote(password)}@{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
