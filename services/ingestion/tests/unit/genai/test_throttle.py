import asyncio

import pytest
from ingestion.genai.shared.throttle import ModelThrottle, TokenBucketRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
async def test_requests_beyond_the_rate_wait_and_are_never_rejected() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(
        requests_per_minute=60, max_burst=1, clock=clock, async_sleep=clock.sleep
    )

    assert await limiter.aacquire() is True
    assert await limiter.aacquire() is True
    assert await limiter.aacquire() is True

    assert len(clock.sleeps) == 2
    assert all(0.99 <= wait <= 1.0 for wait in clock.sleeps)
    assert clock.now == pytest.approx(2.0)
    assert limiter.acquire(blocking=False) is False


@pytest.mark.asyncio
async def test_bucket_refills_while_idle_and_never_exceeds_its_burst() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(
        requests_per_minute=120, max_burst=2, clock=clock, async_sleep=clock.sleep
    )
    assert await limiter.aacquire() and await limiter.aacquire()
    clock.now += 100.0
    for _ in range(2):
        assert await limiter.aacquire()
    assert clock.sleeps == []
    await limiter.aacquire()
    assert len(clock.sleeps) == 1 and clock.sleeps[0] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_in_flight_calls_never_exceed_the_limit_and_all_complete() -> None:
    throttle = ModelThrottle.create(requests_per_minute=6000, max_in_flight=3)
    in_flight = 0
    peak = 0
    release = asyncio.Event()
    started = asyncio.Semaphore(0)

    async def slow_model_call() -> str:
        nonlocal in_flight, peak
        async with throttle.slot():
            in_flight += 1
            peak = max(peak, in_flight)
            started.release()
            await release.wait()
            in_flight -= 1
        return "done"

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(slow_model_call()) for _ in range(10)]
        for _ in range(3):
            await asyncio.wait_for(started.acquire(), timeout=1)
        assert in_flight == 3
        release.set()

    assert peak == 3
    assert [task.result() for task in tasks] == ["done"] * 10


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(requests_per_minute=0)
    with pytest.raises(ValueError):
        ModelThrottle.create(requests_per_minute=60, max_in_flight=0)
