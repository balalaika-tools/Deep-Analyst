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
        if self._released:
            return
        self._released = True
        await self._registry._release(self)

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
    """Atomically grants at most one non-waiting lease per saver thread ID."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_requests: dict[str, str] = {}

    async def try_acquire(self, *, thread_id: str, request_id: str) -> ThreadLease:
        async with self._guard:
            active_request_id = self._active_requests.get(thread_id)
            if active_request_id is not None:
                raise ThreadAlreadyLockedError(active_request_id=active_request_id)

            lock = self._locks.setdefault(thread_id, asyncio.Lock())
            await lock.acquire()
            self._active_requests[thread_id] = request_id
            return ThreadLease(self, thread_id, request_id)

    async def active_request_id(self, thread_id: str) -> str | None:
        async with self._guard:
            return self._active_requests.get(thread_id)

    async def is_locked(self, thread_id: str) -> bool:
        return await self.active_request_id(thread_id) is not None

    async def _release(self, lease: ThreadLease) -> None:
        async with self._guard:
            if self._active_requests.get(lease.thread_id) != lease.request_id:
                return
            self._active_requests.pop(lease.thread_id, None)
            lock = self._locks[lease.thread_id]
            lock.release()
            if not lock.locked() and lease.thread_id not in self._active_requests:
                self._locks.pop(lease.thread_id, None)


__all__ = ["ThreadAlreadyLockedError", "ThreadLease", "ThreadLockRegistry"]
