"""One-shot structured model calls shared by guardrail, verifier, closure, and projection."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from investigation_agent.core.context import RuntimeContext
from investigation_agent.genai.shared.retries import (
    AttemptResult,
    CancellationToken,
    RetryPolicy,
    retry_async,
)


class StructuredRunnable(Protocol):
    async def ainvoke(self, input: object, **kwargs: Any) -> object: ...


type StructuredOutputMethod = Literal["function_calling", "json_schema"]


def _structured_output_method(model: Any) -> StructuredOutputMethod:
    """Prefer provider-native schemas when the model profile advertises support."""

    profile = getattr(model, "profile", None)
    if isinstance(profile, Mapping) and profile.get("structured_output") is True:
        return "json_schema"
    return "function_calling"


class StructuredResultRunner[T: BaseModel](Protocol):
    async def run(
        self, payload: Mapping[str, object] | BaseModel, *, context: RuntimeContext
    ) -> AttemptResult[T]: ...


class StructuredChat:
    """Bind one schema once and invoke it with one isolated user payload."""

    def __init__(self, model: Any, schema: type[BaseModel], system_prompt: str) -> None:
        self._runnable = cast(
            StructuredRunnable,
            model.with_structured_output(
                schema,
                include_raw=False,
                method=_structured_output_method(model),
            ),
        )
        self._schema = schema
        self._system_prompt = system_prompt

    async def invoke(self, payload: Mapping[str, object] | BaseModel) -> BaseModel:
        content = (
            payload.model_dump_json()
            if isinstance(payload, BaseModel)
            else json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        )
        value = await self._runnable.ainvoke(
            [SystemMessage(content=self._system_prompt), HumanMessage(content=content)]
        )
        return self._schema.model_validate(value)


class StructuredRunner[T: BaseModel]:
    """A structured call with the shared bounded transient-retry policy."""

    def __init__(
        self,
        model: Any,
        schema: type[T],
        system_prompt: str,
        *,
        retry_policy: RetryPolicy,
        transient_errors: tuple[type[BaseException], ...],
    ) -> None:
        self._chat = StructuredChat(model, schema, system_prompt)
        self._schema = schema
        self._policy = retry_policy
        self._transient_errors = transient_errors

    async def run(
        self, payload: Mapping[str, object] | BaseModel, *, context: RuntimeContext
    ) -> AttemptResult[T]:
        async def operation(attempt: int) -> T:
            del attempt
            return self._schema.model_validate(await self._chat.invoke(payload))

        return await retry_async(
            operation,
            policy=self._policy,
            retry_on=self._transient_errors,
            cancellation=cancellation_token(context),
            deadline=loop_deadline(context),
        )


def cancellation_token(context: RuntimeContext) -> CancellationToken:
    signal = context.cancellation
    if isinstance(signal, CancellationToken):
        return signal
    return _SignalAdapter(signal)  # type: ignore[return-value]


class _SignalAdapter:
    """Adapt any cooperative cancellation signal to the token API used by retries."""

    def __init__(self, signal: Any) -> None:
        self._signal = signal

    @property
    def cancelled(self) -> bool:
        return bool(self._signal.cancelled)

    def check(self) -> None:
        self._signal.check()


def loop_deadline(context: RuntimeContext) -> float:
    return asyncio.get_running_loop().time() + context.remaining_seconds()


__all__ = [
    "StructuredChat",
    "StructuredResultRunner",
    "StructuredRunner",
    "cancellation_token",
    "loop_deadline",
]
