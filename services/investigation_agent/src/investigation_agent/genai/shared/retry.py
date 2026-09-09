"""Bounded physical retries shared by GenAI calls."""

from __future__ import annotations

import asyncio
import random as random_module
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from investigation_agent.core.context import CancellationSignal, RuntimeContext

# Botocore transport failures do not subclass the built-in TimeoutError/ConnectionError.
BOTOCORE_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    ReadTimeoutError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ConnectionClosedError,
)
TRANSIENT_CLIENT_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "ModelNotReadyException",
        "InternalServerException",
    }
)


class TransientExhaustedError(RuntimeError):
    """All permitted physical attempts failed with an allowlisted transient error."""

    code = "transient_exhausted"

    def __init__(self, message: str, *, attempts: int = 0) -> None:
        self.attempts = attempts
        super().__init__(message)


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


def is_transient_error(exc: BaseException, retry_on: tuple[type[BaseException], ...]) -> bool:
    """Whether an error is explicitly retryable or a retryable Bedrock client failure."""

    if isinstance(exc, retry_on):
        return True
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code")
    return code in TRANSIENT_CLIENT_ERROR_CODES


def retry_deadline(context: RuntimeContext) -> float:
    """Translate the caller's wall-clock budget to the running loop's monotonic clock."""

    return asyncio.get_running_loop().time() + context.remaining_seconds()


async def retry_async[T](
    operation: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retry_on: tuple[type[BaseException], ...],
    cancellation: CancellationSignal,
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
        except BaseException as exc:
            if not is_transient_error(exc, retry_on):
                raise
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


__all__ = [
    "BOTOCORE_TRANSIENT_ERRORS",
    "TRANSIENT_CLIENT_ERROR_CODES",
    "AttemptResult",
    "RetryPolicy",
    "TransientExhaustedError",
    "is_transient_error",
    "retry_async",
    "retry_deadline",
]
