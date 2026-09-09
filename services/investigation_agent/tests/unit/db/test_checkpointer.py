from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from investigation_agent.db.checkpointer import PostgresCheckpointStore
from investigation_agent.domain.investigation_state import (
    ControlState,
    InvestigationState,
    new_turn_state,
)
from investigation_agent.ports.checkpoints import CheckpointStoreError


@dataclass(frozen=True)
class Record:
    checkpoint: Mapping[str, object]
    metadata: Mapping[str, object]


@dataclass
class Saver:
    records: list[Record] = field(default_factory=list)
    error: Exception | None = None
    filters: list[Mapping[str, object] | None] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    async def alist(
        self,
        config: object,
        *,
        filter: Mapping[str, object] | None,
        limit: int,
    ) -> AsyncIterator[Record]:
        del config
        self.filters.append(filter)
        if self.error is not None:
            raise self.error
        for record in self.records[:limit]:
            yield record

    async def adelete_thread(self, thread_id: str) -> None:
        if self.error is not None:
            raise self.error
        self.deleted.append(thread_id)


def _state() -> InvestigationState:
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    turn = new_turn_state(
        turn_id="turn-1",
        request_id="request-1",
        message_id="message-1",
        utterance="Investigate",
        opened_at=opened_at,
    )
    return InvestigationState(control=ControlState(policy_version="v1"), turn=turn)


@pytest.mark.asyncio
async def test_scan_translates_checkpoint_records_into_owned_thread_state() -> None:
    state = _state()
    saver = Saver(
        records=[
            Record(
                checkpoint={"ts": "2026-01-02T00:00:00+00:00", "channel_values": state.as_update()},
                metadata={"app": "investigation", "public_thread_id": "thread-1"},
            )
        ]
    )
    store = PostgresCheckpointStore(cast(Any, saver))

    records = [record async for record in store.scan_threads(limit=10)]

    assert len(records) == 1
    assert records[0].thread_id == "thread-1"
    assert records[0].state == state
    assert saver.filters == [{"app": "investigation"}]


@pytest.mark.asyncio
async def test_provider_failures_are_sanitized_before_crossing_the_port() -> None:
    store = PostgresCheckpointStore(cast(Any, Saver(error=ConnectionError("private dsn"))))

    with pytest.raises(CheckpointStoreError) as captured:
        await store.adelete_thread("thread-1")

    assert "private dsn" not in str(captured.value)
