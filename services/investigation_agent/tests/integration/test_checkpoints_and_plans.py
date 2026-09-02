"""Real-role checkpoint deletion and index-plan checks against the disposable database."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

import pytest
from investigation_agent.adapters.postgres.checkpointer import create_checkpointer
from investigation_agent.adapters.postgres.pools import DatabasePools
from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.invoke_turn import ThreadNotFound, graph_config
from investigation_agent.application.thread_locks import ThreadLockRegistry
from langgraph.checkpoint.base import empty_checkpoint


class _SnapshotGraph:
    """Minimal ``aget_state`` over the saver so deletion can check thread existence."""

    def __init__(self, saver: Any) -> None:
        self._saver = saver

    async def aget_state(self, config: Mapping[str, object]) -> Any:
        record = await self._saver.aget_tuple(dict(config))
        values = record.checkpoint.get("channel_values", {}) if record else {}

        class _Snapshot:
            pass

        snapshot = _Snapshot()
        snapshot.values = values  # type: ignore[attr-defined]
        return snapshot

    def astream(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("deletion never streams")


async def _put(saver: Any, thread_id: str) -> None:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"control": {"policy_version": "v1"}, "messages": []}
    checkpoint["channel_versions"] = {"control": "1", "messages": "1"}
    config = graph_config(thread_id=thread_id)
    await saver.aput(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        checkpoint,
        {"source": "input", "step": 0, **config["metadata"]},
        {"control": "1", "messages": "1"},
    )


@pytest.mark.asyncio
async def test_thread_deletion_removes_only_that_thread_and_reads_404_afterwards(
    database_pools: DatabasePools,
) -> None:
    saver = create_checkpointer(database_pools.writer)
    await _put(saver, "thread-delete-a")
    await _put(saver, "thread-delete-b")
    listed = [
        cast(Mapping[str, Any], record.metadata)["public_thread_id"]
        async for record in saver.alist(None, filter={"app": "investigation"})
    ]
    assert {"thread-delete-a", "thread-delete-b"} <= set(listed)

    deleter = DeleteThread(
        graph=_SnapshotGraph(saver), checkpointer=saver, locks=ThreadLockRegistry()
    )
    await deleter.delete("thread-delete-a")

    assert await saver.aget_tuple({"configurable": {"thread_id": "thread-delete-a"}}) is None
    assert await saver.aget_tuple({"configurable": {"thread_id": "thread-delete-b"}}) is not None
    with pytest.raises(ThreadNotFound):
        await deleter.delete("thread-delete-a")
    async with database_pools.reader.connection() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT count(*) AS n FROM public.records")
            row = await cursor.fetchone()
    assert row is not None and row["n"] > 0


@pytest.mark.asyncio
async def test_representative_reads_use_the_expected_indexes(database_pools: DatabasePools) -> None:
    plans = {
        "bm25": (
            "SELECT c.chunk_id FROM public.chunks c "
            "WHERE c.text @@@ paradedb.match('text', $1, conjunction_mode => true) LIMIT 5",
            ("transfer",),
        ),
        "vector": (
            "SELECT c.chunk_id FROM public.chunks c "
            "ORDER BY c.embedding <=> $1::public.vector LIMIT 5",
            ("[1,0,0,0]",),
        ),
        "structured": (
            "SELECT record_id FROM public.transactions WHERE amount_minor >= $1",
            (100,),
        ),
        "graph": (
            "SELECT relationship_id FROM public.relationships "
            "WHERE subject_entity_id = ANY($1::text[]) "
            "OR object_entity_id = ANY($1::text[])",
            (["e-1"],),
        ),
    }
    explained: dict[str, str] = {}
    async with database_pools.reader.connection() as connection:
        async with connection.transaction():
            async with connection.cursor() as cursor:
                await cursor.execute("SET TRANSACTION READ ONLY")
                for name, (sql, params) in plans.items():
                    await cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}", params)
                    row = await cursor.fetchone()
                    assert row is not None
                    explained[name] = json.dumps(next(iter(row.values())))
    assert "Custom Scan" in explained["bm25"] or "ParadeDB" in explained["bm25"]
    assert "Index Scan" in explained["vector"] or "Seq Scan" in explained["vector"]
    for name in ("structured", "graph"):
        assert "Scan" in explained[name]
