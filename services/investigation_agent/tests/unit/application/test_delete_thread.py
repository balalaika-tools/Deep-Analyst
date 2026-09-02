from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from investigation_agent.application.delete_thread import DeleteThread
from investigation_agent.application.invoke_turn import ThreadBusy, ThreadNotFound
from investigation_agent.application.thread_locks import ThreadLockRegistry
from investigation_agent.core.errors import DependencyUnavailableFailure, InvestigationFailure


@dataclass(frozen=True)
class Snapshot:
    values: Mapping[str, Any]


@dataclass
class Graph:
    known: set[str] = field(default_factory=set)

    async def aget_state(self, config: Mapping[str, object]) -> Snapshot:
        thread_id = config["configurable"]["thread_id"]  # type: ignore[index]
        return Snapshot({"control": {"case_id": "case-1"}} if thread_id in self.known else {})

    def astream(self, *args: Any, **kwargs: Any) -> Any:
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

    await DeleteThread(graph=graph, checkpointer=checkpointer, locks=locks).delete("thread-1")

    assert checkpointer.deleted == ["thread-1"]
    assert not await locks.is_locked("thread-1")


@pytest.mark.asyncio
async def test_unknown_thread_is_not_found_and_nothing_is_deleted() -> None:
    checkpointer = Checkpointer()
    with pytest.raises(ThreadNotFound):
        await DeleteThread(
            graph=Graph(), checkpointer=checkpointer, locks=ThreadLockRegistry()
        ).delete("thread-9")
    assert checkpointer.deleted == []


@pytest.mark.asyncio
async def test_executing_thread_is_busy_and_untouched() -> None:
    locks = ThreadLockRegistry()
    lease = await locks.try_acquire(thread_id="thread-1", request_id="request-1")
    checkpointer = Checkpointer()

    with pytest.raises(ThreadBusy):
        await DeleteThread(
            graph=Graph({"thread-1"}), checkpointer=checkpointer, locks=locks
        ).delete("thread-1")

    assert checkpointer.deleted == []
    await lease.release()


@pytest.mark.asyncio
async def test_checkpointer_failure_is_translated_and_the_lock_is_released() -> None:
    locks = ThreadLockRegistry()
    from investigation_agent.core.errors import AdapterDependencyUnavailableError

    with pytest.raises(InvestigationFailure) as captured:
        await DeleteThread(
            graph=Graph({"thread-1"}),
            checkpointer=Checkpointer(error=AdapterDependencyUnavailableError("host secret")),
            locks=locks,
        ).delete("thread-1")

    assert isinstance(captured.value, DependencyUnavailableFailure)
    assert "secret" not in str(captured.value)
    assert not await locks.is_locked("thread-1")
