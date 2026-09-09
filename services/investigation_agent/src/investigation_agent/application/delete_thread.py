"""Delete every checkpoint of one thread through the public checkpointer API."""

from __future__ import annotations

from investigation_agent.application.invoke_turn import ThreadBusy, ThreadNotFound
from investigation_agent.application.thread_locks import (
    ThreadAlreadyLockedError,
    ThreadLockRegistry,
)
from investigation_agent.ports.checkpoints import ThreadStateDeleter
from investigation_agent.ports.investigator import Investigator

DELETE_REQUEST_ID = "__delete__"


class DeleteThread:
    """Acquire the thread lock, refuse busy or unknown threads, then delete irrecoverably."""

    def __init__(
        self,
        *,
        investigator: Investigator,
        store: ThreadStateDeleter,
        locks: ThreadLockRegistry,
    ) -> None:
        self._investigator = investigator
        self._store = store
        self._locks = locks

    async def delete(self, thread_id: str) -> None:
        try:
            lease = await self._locks.try_acquire(thread_id=thread_id, request_id=DELETE_REQUEST_ID)
        except ThreadAlreadyLockedError:
            raise ThreadBusy from None
        try:
            state = await self._investigator.load_state(thread_id)
            if state is None:
                raise ThreadNotFound
            await self._store.adelete_thread(thread_id)
        finally:
            await lease.release()


__all__ = ["DELETE_REQUEST_ID", "DeleteThread"]
