from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast

import psycopg
import pytest
from investigation_agent.adapters.postgres.evidence_reader import PostgresEvidenceReader
from investigation_agent.adapters.postgres.pools import DatabasePools, probe_database_readiness
from investigation_agent.genai.evidence_search.retrieval import FusionPolicy, retrieve_hybrid
from investigation_agent.genai.evidence_search.schemas import RetrievalQuery
from investigation_agent.genai.investigation.connections import (
    ConnectionFilters,
    FindConnections,
    FindConnectionsInput,
    GraphLimits,
)
from investigation_agent.genai.record_query.executor import (
    ExecutorLimits,
    ReaderPool,
    execute_guarded_select,
)
from investigation_agent.genai.record_query.schemas import SqlPlan


class _Embedder:
    async def embed(self, text: str, *, deadline: float) -> Sequence[float]:
        del text, deadline
        return [1.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_real_roles_enforce_privilege_matrix_and_readiness(
    database_pools: DatabasePools,
) -> None:
    readiness = await probe_database_readiness(
        database_pools,
        expected_initializer_version="agent-runtime@2",
        timeout_s=5,
    )
    assert readiness.ready

    await _assert_rejected(
        database_pools.reader,
        "INSERT INTO public.records "
        "(record_id, source_system, source_record_id, record_type, payload, "
        "source_path, content_hash) VALUES "
        "('forbidden', 'x', 'x', 'x', '{}', '/x', 'aaaaaaaa')",
    )
    await _assert_rejected(database_pools.reader, "CREATE TEMP TABLE shadow(record_id text)")
    await _assert_rejected(database_pools.reader, "SELECT * FROM agent_runtime.checkpoints")
    await _assert_rejected(database_pools.writer, "SELECT * FROM public.records")
    await _assert_rejected(
        database_pools.writer,
        "UPDATE agent_runtime.schema_version SET version = 'weakened'",
    )


@pytest.mark.asyncio
async def test_global_view_is_readable_across_reused_reader_connection(
    database_pools: DatabasePools,
) -> None:
    plan = SqlPlan(
        sql=(
            "SELECT record_id, content_hash, source_refs, amount_minor "
            "FROM agent_read.transactions_v1"
        ),
        expected_shape="rows",
    )
    deadline = asyncio.get_running_loop().time() + 10
    reader_pool = cast(ReaderPool, database_pools.reader)
    first = await execute_guarded_select(
        pool=reader_pool,
        plan=plan,
        deadline=deadline,
        limits=ExecutorLimits(max_rows=10, max_bytes=20_000),
    )
    second = await execute_guarded_select(
        pool=reader_pool,
        plan=plan,
        deadline=deadline,
        limits=ExecutorLimits(max_rows=10, max_bytes=20_000),
    )
    assert {
        field.value for row in first.rows for field in row.fields if field.name == "record_id"
    } == {
        "bank:a",
        "bank:b",
    }
    assert second.rows == first.rows
    async with database_pools.reader.connection() as connection:
        async with connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT * FROM agent_read.transactions_v1")
                assert len(await cursor.fetchall()) == 2


@pytest.mark.asyncio
async def test_hybrid_search_and_graph_reads_cover_the_global_corpus(
    database_pools: DatabasePools,
) -> None:
    reader = PostgresEvidenceReader(database_pools.reader)
    deadline = asyncio.get_running_loop().time() + 10
    retrieval = await retrieve_hybrid(
        reader=reader,
        embedder=_Embedder(),
        query=RetrievalQuery(query="alpha", top_k=10),
        excluded_chunk_ids=frozenset(),
        deadline=deadline,
        policy=FusionPolicy(),
    )
    assert {item.chunk_id for item in retrieval.candidates} == {"chunk:bank:a", "chunk:bank:b"}
    assert len(retrieval.candidates[0].contributions) == 2

    graph = await FindConnections(
        reader=reader,
        server_limits=GraphLimits(
            max_depth=3,
            max_paths=10,
            max_nodes=10,
            max_edges=10,
            max_rows=30,
        ),
    ).run(
        call_id="graph-integration",
        request=FindConnectionsInput(
            seed_entity_ids=("bank:a:entity:1", "bank:b:entity:1"),
            filters=ConnectionFilters(),
            max_depth=2,
            max_paths=5,
            max_nodes=10,
            max_edges=10,
            max_rows=30,
        ),
        deadline=deadline,
    )
    assert graph.status == "connections_found"
    assert {node.entity_id for node in graph.nodes} == {
        "bank:a:entity:1",
        "bank:a:entity:2",
        "bank:b:entity:1",
        "bank:b:entity:2",
    }


async def _assert_rejected(pool: object, statement: str) -> None:
    with pytest.raises(
        (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)
    ):
        async with pool.connection() as connection:  # type: ignore[attr-defined]
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(statement)
