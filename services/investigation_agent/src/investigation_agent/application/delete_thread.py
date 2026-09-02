"""Delete every checkpoint of one thread through the public checkpointer API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from investigation_agent.application.invoke_turn import (
    InvocationGraph,
    ThreadBusy,
    ThreadNotFound,
    graph_config,
)
from investigation_agent.application.thread_locks import (
    ThreadAlreadyLockedError,
    ThreadLockRegistry,
)
from investigation_agent.core.errors import translate_adapter_error

DELETE_REQUEST_ID = "__delete__"


class ThreadDeleter(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


class DeleteThread:
    """Acquire the thread lock, refuse busy or unknown threads, then delete irrecoverably."""

    def __init__(
        self,
        *,
        graph: InvocationGraph,
        checkpointer: ThreadDeleter,
        locks: ThreadLockRegistry,
    ) -> None:
        self._graph = graph
        self._checkpointer = checkpointer
        self._locks = locks

    async def delete(self, thread_id: str) -> None:
        try:
            lease = await self._locks.try_acquire(thread_id=thread_id, request_id=DELETE_REQUEST_ID)
        except ThreadAlreadyLockedError:
            raise ThreadBusy from None
        try:
            config: Mapping[str, Any] = graph_config(thread_id=thread_id, case_id="-")
            snapshot = await self._graph.aget_state(config)
            if not snapshot.values or not snapshot.values.get("control"):
                raise ThreadNotFound
            try:
                await self._checkpointer.adelete_thread(thread_id)
            except Exception as exc:
                raise translate_adapter_error(exc) from None
        finally:
            await lease.release()


__all__ = ["DELETE_REQUEST_ID", "DeleteThread", "ThreadDeleter"]
