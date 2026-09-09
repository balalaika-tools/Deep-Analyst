"""Transient-error classification covers botocore and Bedrock client errors."""

from __future__ import annotations

import asyncio

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from investigation_agent.core.context import CancellationController
from investigation_agent.genai.shared.middleware import model_retry_middleware
from investigation_agent.genai.shared.retry import (
    RetryPolicy,
    TransientExhaustedError,
    is_transient_error,
    retry_async,
)

POLICY = RetryPolicy(
    max_attempts=3, initial_delay_s=0, backoff_factor=1, max_delay_s=0, jitter=False
)


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "provider"}}, "InvokeModel")


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (_client_error("ThrottlingException"), True),
        (_client_error("ServiceUnavailableException"), True),
        (_client_error("ModelNotReadyException"), True),
        (_client_error("AccessDeniedException"), False),
        (_client_error("ValidationException"), False),
        (ReadTimeoutError(endpoint_url="https://bedrock"), True),
        (ValueError("bad"), False),
    ],
)
def test_is_transient_error_recognises_bedrock_codes_only(exc: Exception, transient: bool) -> None:
    assert is_transient_error(exc, (TimeoutError, ReadTimeoutError)) is transient


@pytest.mark.asyncio
async def test_retry_async_retries_throttling_but_not_access_denied() -> None:
    attempts: list[int] = []

    async def throttled(attempt: int) -> str:
        attempts.append(attempt)
        if attempt < 3:
            raise _client_error("ThrottlingException")
        return "ok"

    async def denied(attempt: int) -> str:
        raise _client_error("AccessDeniedException")

    deadline = asyncio.get_running_loop().time() + 5
    result = await retry_async(
        throttled,
        policy=POLICY,
        retry_on=(TimeoutError,),
        cancellation=CancellationController.create(),
        deadline=deadline,
    )
    assert result.value == "ok" and attempts == [1, 2, 3]

    with pytest.raises(ClientError):
        await retry_async(
            denied,
            policy=POLICY,
            retry_on=(TimeoutError,),
            cancellation=CancellationController.create(),
            deadline=deadline,
        )


@pytest.mark.asyncio
async def test_retry_async_still_exhausts_on_persistent_throttling() -> None:
    async def always(attempt: int) -> str:
        raise _client_error("TooManyRequestsException")

    with pytest.raises(TransientExhaustedError) as info:
        await retry_async(
            always,
            policy=POLICY,
            retry_on=(),
            cancellation=CancellationController.create(),
            deadline=asyncio.get_running_loop().time() + 5,
        )
    assert info.value.attempts == 3


@pytest.mark.asyncio
async def test_retry_async_honours_the_shared_cancellation_contract() -> None:
    cancellation = CancellationController.create()
    cancellation.cancel()

    async def should_not_run(attempt: int) -> str:
        raise AssertionError(f"started cancelled attempt {attempt}")

    with pytest.raises(asyncio.CancelledError):
        await retry_async(
            should_not_run,
            policy=POLICY,
            retry_on=(TimeoutError,),
            cancellation=cancellation,
            deadline=asyncio.get_running_loop().time() + 5,
        )


def test_model_retry_middleware_uses_the_shared_predicate() -> None:
    middleware = model_retry_middleware(POLICY, (TimeoutError,))

    assert callable(middleware.retry_on)
    assert middleware.retry_on(_client_error("ThrottlingException"))
    assert middleware.retry_on(TimeoutError())
    assert not middleware.retry_on(_client_error("AccessDeniedException"))
