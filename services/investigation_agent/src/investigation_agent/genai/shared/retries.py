"""Bounded physical retries and cooperative cancellation for model and tool calls."""

from __future__ import annotations

import asyncio
import random as random_module
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
from langchain_core.tools import BaseTool


class OperationCancelledError(asyncio.CancelledError):
    """Cooperative cancellation observed before starting another physical attempt."""


class TransientExhaustedError(RuntimeError):
    """All permitted physical attempts failed with an allowlisted transient error."""

    code = "transient_exhausted"

    def __init__(self, message: str, *, attempts: int = 0) -> None:
        self.attempts = attempts
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CancellationToken:
    event: asyncio.Event

    @classmethod
    def create(cls) -> CancellationToken:
        return cls(asyncio.Event())

    def cancel(self) -> None:
        self.event.set()

    @property
    def cancelled(self) -> bool:
        return self.event.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise OperationCancelledError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    initial_delay_s: float
    backoff_factor: float
    max_delay_s: float
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.initial_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("retry delays cannot be negative")
        if self.backoff_factor < 1:
            raise ValueError("backoff_factor must be at least one")


@dataclass(frozen=True, slots=True)
class AttemptResult[T]:
    value: T
    attempts: int


async def retry_async[T](
    operation: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retry_on: tuple[type[BaseException], ...],
    cancellation: CancellationToken,
    deadline: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random: Callable[[], float] = random_module.random,
    on_attempt: Callable[[int, BaseException | None], None] | None = None,
) -> AttemptResult[T]:
    """Retry one identical physical operation; semantic revision remains with its caller."""

    last_error: BaseException | None = None
    attempts_started = 0
    for attempt in range(1, policy.max_attempts + 1):
        cancellation.check()
        if monotonic() >= deadline:
            raise TransientExhaustedError(
                "operation deadline exhausted",
                attempts=attempts_started,
            ) from last_error
        attempts_started = attempt
        try:
            value = await operation(attempt)
        except retry_on as exc:
            last_error = exc
            if on_attempt:
                on_attempt(attempt, exc)
            if attempt >= policy.max_attempts:
                break
            delay = _retry_delay(policy, attempt, random())
            cancellation.check()
            remaining = deadline - monotonic()
            if remaining <= 0 or delay >= remaining:
                break
            await sleep(delay)
        else:
            if on_attempt:
                on_attempt(attempt, None)
            return AttemptResult(value=value, attempts=attempt)
    raise TransientExhaustedError(
        "transient operation attempts exhausted",
        attempts=attempts_started,
    ) from last_error


def _retry_delay(policy: RetryPolicy, failed_attempt: int, random_value: float) -> float:
    delay = min(
        policy.max_delay_s,
        policy.initial_delay_s * policy.backoff_factor ** (failed_attempt - 1),
    )
    return delay * (0.5 + random_value / 2) if policy.jitter else delay


def model_retry_middleware(
    policy: RetryPolicy, retry_on: tuple[type[Exception], ...]
) -> ModelRetryMiddleware:
    """Build LangChain's per-model physical retry layer from validated policy."""

    return ModelRetryMiddleware(
        max_retries=policy.max_attempts - 1,
        retry_on=retry_on,
        backoff_factor=policy.backoff_factor,
        initial_delay=policy.initial_delay_s,
        max_delay=policy.max_delay_s,
        jitter=policy.jitter,
        on_failure="error",
    )


def tool_retry_middleware(
    policy: RetryPolicy,
    retry_on: tuple[type[Exception], ...],
    *,
    tools: list[BaseTool | str] | None = None,
) -> ToolRetryMiddleware:
    """Build retry middleware for idempotent low-level tactical-agent tools only."""

    return ToolRetryMiddleware(
        max_retries=policy.max_attempts - 1,
        tools=tools,
        retry_on=retry_on,
        backoff_factor=policy.backoff_factor,
        initial_delay=policy.initial_delay_s,
        max_delay=policy.max_delay_s,
        jitter=policy.jitter,
        on_failure="error",
    )


__all__ = [
    "AttemptResult",
    "CancellationToken",
    "OperationCancelledError",
    "RetryPolicy",
    "TransientExhaustedError",
    "model_retry_middleware",
    "retry_async",
    "tool_retry_middleware",
]
