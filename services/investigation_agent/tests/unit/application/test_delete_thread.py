from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.invoke_turn import ThreadBusy, ThreadNotFound
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.domain.investigation_state import ControlState, InvestigationState
from investigation_agent.ports.checkpoints import CheckpointStoreError
from investigation_agent.ports.investigator import InvestigationError


@dataclass
class Graph:
    known: set[str] = field(default_factory=set)

    async def load_state(self, thread_id: str) -> InvestigationState | None:
        if thread_id not in self.known:
            return None
        return InvestigationState(control=ControlState(policy_version="v1"))

    def stream_turn(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("deletion never streams")


@dataclass
class Checkpointer:
    deleted: list[str] = field(default_factory=list)
    error: Exception | None = None

    async def adelete_thread(self, thread_id: str) -> None:
        if self.error is not None:
            raise self.error
        self.deleted.append(thread_id)


@pytest.mark.asyncio
async def test_idle_thread_is_deleted_through_the_checkpointer_and_the_lock_is_released() -> None:
    graph = Graph({"thread-1", "thread-2"})
    checkpointer = Checkpointer()
    locks = ThreadLockRegistry()

    await DeleteThread(investigator=graph, store=checkpointer, locks=locks).delete("thread-1")

    assert checkpointer.deleted == ["thread-1"]
    assert not await locks.is_locked("thread-1")


@pytest.mark.asyncio
async def test_unknown_thread_is_not_found_and_nothing_is_deleted() -> None:
    checkpointer = Checkpointer()
    with pytest.raises(ThreadNotFound):
        await DeleteThread(
            investigator=Graph(),
            store=checkpointer,
            locks=ThreadLockRegistry(),
        ).delete("thread-9")
    assert checkpointer.deleted == []


@pytest.mark.asyncio
async def test_executing_thread_is_busy_and_untouched() -> None:
    locks = ThreadLockRegistry()
    lease = await locks.try_acquire(thread_id="thread-1", request_id="request-1")
    checkpointer = Checkpointer()

    with pytest.raises(ThreadBusy):
        await DeleteThread(
            investigator=Graph({"thread-1"}),
            store=checkpointer,
            locks=locks,
        ).delete("thread-1")

    assert checkpointer.deleted == []
    await lease.release()


@pytest.mark.asyncio
async def test_checkpointer_failure_is_translated_and_the_lock_is_released() -> None:
    locks = ThreadLockRegistry()

    with pytest.raises(CheckpointStoreError) as captured:
        await DeleteThread(
            investigator=Graph({"thread-1"}),
            store=Checkpointer(error=CheckpointStoreError()),
            locks=locks,
        ).delete("thread-1")

    assert "secret" not in str(captured.value)
    assert not await locks.is_locked("thread-1")


@dataclass
class BrokenGraph(Graph):
    error: Exception | None = None

    async def load_state(self, thread_id: str) -> InvestigationState | None:
        del thread_id
        assert self.error is not None
        raise self.error


@pytest.mark.asyncio
async def test_state_read_failure_is_translated_and_the_lock_is_released() -> None:
    locks = ThreadLockRegistry()
    checkpointer = Checkpointer()

    with pytest.raises(InvestigationError) as captured:
        await DeleteThread(
            investigator=BrokenGraph(error=InvestigationError()),
            store=checkpointer,
            locks=locks,
        ).delete("thread-1")

    assert "secret" not in str(captured.value)
    assert checkpointer.deleted == []
    assert not await locks.is_locked("thread-1")
