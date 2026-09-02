"""Single-replica serialization for investigation thread execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class ThreadAlreadyLockedError(RuntimeError):
    """The thread already has one executor in this process."""

    def __init__(self, *, active_request_id: str) -> None:
        super().__init__("thread already has an active executor")
        self.active_request_id = active_request_id


@dataclass(slots=True)
class ThreadLease:
    """Idempotently releases one registry-owned thread lock."""

    _registry: ThreadLockRegistry
    thread_id: str
    request_id: str
    _released: bool = False

    async def release(self) -> None:
        # Nothing here suspends: a cancellation delivered between marking the lease released
        # and freeing the registry lock would otherwise leak the thread lock permanently.
        if self._released:
            return
        self._registry._release(self)
        self._released = True

    async def __aenter__(self) -> ThreadLease:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        await self.release()


class ThreadLockRegistry:
    """Atomically grants at most one non-waiting lease per saver thread ID.

    Every mutation runs without suspending, so single-event-loop atomicity needs no guard lock
    and no code path can be cancelled halfway through a state change.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_requests: dict[str, str] = {}

    async def try_acquire(self, *, thread_id: str, request_id: str) -> ThreadLease:
        active_request_id = self._active_requests.get(thread_id)
        if active_request_id is not None:
            raise ThreadAlreadyLockedError(active_request_id=active_request_id)

        lock = self._locks.setdefault(thread_id, asyncio.Lock())
        # Never contended: a locked entry always has an active request, which was rejected above.
        await lock.acquire()
        self._active_requests[thread_id] = request_id
        return ThreadLease(self, thread_id, request_id)

    async def active_request_id(self, thread_id: str) -> str | None:
        return self._active_requests.get(thread_id)

    async def is_locked(self, thread_id: str) -> bool:
        return await self.active_request_id(thread_id) is not None

    def _release(self, lease: ThreadLease) -> None:
        if self._active_requests.get(lease.thread_id) != lease.request_id:
            return
        self._active_requests.pop(lease.thread_id, None)
        lock = self._locks[lease.thread_id]
        lock.release()
        if not lock.locked():
            self._locks.pop(lease.thread_id, None)


__all__ = ["ThreadAlreadyLockedError", "ThreadLease", "ThreadLockRegistry"]
