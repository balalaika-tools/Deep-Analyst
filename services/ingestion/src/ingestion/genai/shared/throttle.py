"""Process-wide model throttle: a token bucket for request rate and a semaphore for in-flight calls.

One instance is shared by entity extraction, relationship extraction, and embeddings so
the three together stay under the provider quota. The bucket is a LangChain rate
limiter, so it also applies to every retry the chat model makes; the semaphore bounds
logical calls.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Self

from langchain_core.rate_limiters import BaseRateLimiter

from ingestion.config.settings import Settings


class TokenBucketRateLimiter(BaseRateLimiter):
    """LangChain's in-memory token bucket with an injectable clock and sleep.

    `InMemoryRateLimiter` reads `time.monotonic` directly, which cannot be controlled in
    a test; this keeps the same semantics behind the same interface.
    """

    def __init__(
        self,
        *,
        requests_per_minute: int,
        max_burst: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        blocking_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self._rate = requests_per_minute / 60.0
        self._capacity = max_burst if max_burst is not None else max(1.0, requests_per_minute / 10)
        self._tokens = self._capacity
        self._clock = clock
        self._async_sleep = async_sleep
        self._blocking_sleep = blocking_sleep
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now

    def _consume(self) -> bool:
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def _wait_seconds(self) -> float:
        return max((1.0 - self._tokens) / self._rate, 0.001)

    def acquire(self, *, blocking: bool = True) -> bool:
        while not self._consume():
            if not blocking:
                return False
            self._blocking_sleep(self._wait_seconds())
        return True

    async def aacquire(self, *, blocking: bool = True) -> bool:
        while not self._consume():
            if not blocking:
                return False
            await self._async_sleep(self._wait_seconds())
        return True


@dataclass(slots=True)
class ModelThrottle:
    rate_limiter: TokenBucketRateLimiter
    max_in_flight: int
    _in_flight: asyncio.Semaphore

    @classmethod
    def create(cls, *, requests_per_minute: int, max_in_flight: int) -> Self:
        if max_in_flight <= 0:
            raise ValueError("max_in_flight must be positive")
        return cls(
            rate_limiter=TokenBucketRateLimiter(requests_per_minute=requests_per_minute),
            max_in_flight=max_in_flight,
            _in_flight=asyncio.Semaphore(max_in_flight),
        )

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one in-flight slot for a logical model call; excess callers wait."""
        async with self._in_flight:
            yield

    async def wait_for_request(self) -> None:
        """Block until the rate limit allows one more physical request."""
        await self.rate_limiter.aacquire()


def build_throttle(settings: Settings) -> ModelThrottle:
    return ModelThrottle.create(
        requests_per_minute=settings.llm_requests_per_minute,
        max_in_flight=settings.llm_max_in_flight,
    )
