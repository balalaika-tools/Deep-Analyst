from __future__ import annotations

import asyncio
import contextlib

import pytest
from investigation_agent.application.thread_locks import (
    ThreadAlreadyLockedError,
    ThreadLockRegistry,
)


@pytest.mark.asyncio
async def test_release_completes_without_suspending_so_cancellation_cannot_leak_the_lock() -> None:
    registry = ThreadLockRegistry()
    lease = await registry.try_acquire(thread_id="thread-1", request_id="request-1")

    # A cancellation is only ever delivered at a suspension point, so a release that finishes
    # in a single step has no window in which the lease is marked released but the lock is held.
    release = lease.release()
    with pytest.raises(StopIteration):
        release.send(None)

    assert not await registry.is_locked("thread-1")
    await lease.release()
    assert not await registry.is_locked("thread-1")


@pytest.mark.asyncio
async def test_task_cancelled_while_releasing_still_frees_the_lock() -> None:
    registry = ThreadLockRegistry()
    lease = await registry.try_acquire(thread_id="thread-1", request_id="request-1")
    started = asyncio.Event()

    async def release_then_wait() -> None:
        started.set()
        await lease.release()
        await asyncio.Event().wait()

    task = asyncio.create_task(release_then_wait())
    await started.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not await registry.is_locked("thread-1")
    await registry.try_acquire(thread_id="thread-1", request_id="request-2")


@pytest.mark.asyncio
async def test_second_acquire_is_rejected_with_the_active_request() -> None:
    registry = ThreadLockRegistry()
    await registry.try_acquire(thread_id="thread-1", request_id="request-1")

    with pytest.raises(ThreadAlreadyLockedError) as captured:
        await registry.try_acquire(thread_id="thread-1", request_id="request-2")

    assert captured.value.active_request_id == "request-1"
