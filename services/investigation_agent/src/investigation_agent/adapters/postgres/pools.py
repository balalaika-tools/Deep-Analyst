"""Purpose-specific PostgreSQL pools and bounded read-only readiness probes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection, AsyncRawCursor
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

type AgentPool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class PoolBounds:
    min_size: int
    max_size: int
    acquisition_timeout_s: float
    max_waiting: int = 32

    def __post_init__(self) -> None:
        if self.min_size < 0 or self.max_size < 1 or self.min_size > self.max_size:
            raise ValueError("invalid PostgreSQL pool size bounds")
        if self.acquisition_timeout_s <= 0 or self.max_waiting < 1:
            raise ValueError("pool timeouts and waiter bounds must be positive")


@dataclass(frozen=True, slots=True)
class DatabasePools:
    reader: AgentPool
    writer: AgentPool

    async def open(self) -> None:
        await asyncio.gather(self.reader.open(wait=True), self.writer.open(wait=True))

    async def close(self, *, timeout_s: float = 10.0) -> None:
        async with asyncio.timeout(timeout_s):
            await asyncio.gather(self.reader.close(), self.writer.close())


async def _configure_reader(connection: AsyncConnection[dict[str, Any]]) -> None:
    await register_vector_async(connection)


def create_reader_pool(*, dsn: str, bounds: PoolBounds, name: str = "agent-reader") -> AgentPool:
    """Construct unopened reader pool; bootstrap owns the explicit open lifecycle."""

    return AsyncConnectionPool(
        conninfo=_native_dsn(dsn),
        connection_class=AsyncConnection,
        min_size=bounds.min_size,
        max_size=bounds.max_size,
        timeout=bounds.acquisition_timeout_s,
        max_waiting=bounds.max_waiting,
        open=False,
        name=name,
        configure=_configure_reader,
        kwargs={
            "autocommit": False,
            "row_factory": dict_row,
            "cursor_factory": AsyncRawCursor,
            "prepare_threshold": 0,
            "options": "-c search_path=pg_catalog,public",
        },
    )


def create_writer_pool(*, dsn: str, bounds: PoolBounds, name: str = "agent-writer") -> AgentPool:
    """Construct unopened saver pool with the settings required by AsyncPostgresSaver."""

    return AsyncConnectionPool(
        conninfo=_native_dsn(dsn),
        connection_class=AsyncConnection,
        min_size=bounds.min_size,
        max_size=bounds.max_size,
        timeout=bounds.acquisition_timeout_s,
        max_waiting=bounds.max_waiting,
        open=False,
        name=name,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
            "options": "-c search_path=agent_runtime,pg_catalog",
        },
    )


def create_database_pools(
    *,
    reader_dsn: str,
    writer_dsn: str,
    reader_bounds: PoolBounds,
    writer_bounds: PoolBounds,
) -> DatabasePools:
    return DatabasePools(
        reader=create_reader_pool(dsn=reader_dsn, bounds=reader_bounds),
        writer=create_writer_pool(dsn=writer_dsn, bounds=writer_bounds),
    )


@dataclass(frozen=True, slots=True)
class ReadinessComponent:
    ready: bool
    code: str


@dataclass(frozen=True, slots=True)
class DatabaseReadiness:
    reader: ReadinessComponent
    writer: ReadinessComponent

    @property
    def ready(self) -> bool:
        return self.reader.ready and self.writer.ready


async def probe_database_readiness(
    pools: DatabasePools,
    *,
    expected_initializer_version: str,
    timeout_s: float,
) -> DatabaseReadiness:
    """Check both role surfaces concurrently; every statement is read-only."""

    try:
        async with asyncio.timeout(timeout_s):
            reader, writer = await asyncio.gather(
                _probe_reader(pools.reader),
                _probe_writer(pools.writer, expected_initializer_version),
            )
    except TimeoutError:
        timed_out = ReadinessComponent(False, "readiness_timeout")
        return DatabaseReadiness(reader=timed_out, writer=timed_out)
    return DatabaseReadiness(reader=reader, writer=writer)


async def _probe_reader(pool: AgentPool) -> ReadinessComponent:
    try:
        async with pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute("SET TRANSACTION READ ONLY")
                    await cursor.execute(
                        "SELECT "
                        "to_regclass('agent_read.transactions_v1') IS NOT NULL AS transactions, "
                        "to_regclass('agent_read.accounts_v1') IS NOT NULL AS accounts, "
                        "to_regclass('agent_read.communications_v1') IS NOT NULL AS communications, "
                        "to_regclass('public.chunks_text_bm25') IS NOT NULL AS bm25, "
                        "to_regclass('public.chunks_embedding_hnsw') IS NOT NULL AS vector_index, "
                        "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS vector_ext, "
                        "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search') AS search_ext"
                    )
                    row = await cursor.fetchone()
        if not isinstance(row, Mapping) or not all(bool(value) for value in row.values()):
            return ReadinessComponent(False, "reader_dependency_missing")
        return ReadinessComponent(True, "ready")
    except Exception:
        return ReadinessComponent(False, "reader_unavailable")


async def _probe_writer(pool: AgentPool, expected_version: str) -> ReadinessComponent:
    try:
        async with pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute("SET TRANSACTION READ ONLY")
                    await cursor.execute(
                        "SELECT version FROM agent_runtime.schema_version WHERE singleton = true"
                    )
                    row = await cursor.fetchone()
        if not isinstance(row, Mapping) or row.get("version") != expected_version:
            return ReadinessComponent(False, "initializer_version_mismatch")
        return ReadinessComponent(True, "ready")
    except Exception:
        return ReadinessComponent(False, "writer_unavailable")


def _native_dsn(dsn: str) -> str:
    """Psycopg does not accept SQLAlchemy's ``postgresql+psycopg`` scheme."""

    prefix = "postgresql+psycopg://"
    return f"postgresql://{dsn.removeprefix(prefix)}" if dsn.startswith(prefix) else dsn


__all__ = [
    "AgentPool",
    "DatabasePools",
    "DatabaseReadiness",
    "PoolBounds",
    "ReadinessComponent",
    "create_database_pools",
    "create_reader_pool",
    "create_writer_pool",
    "probe_database_readiness",
]
