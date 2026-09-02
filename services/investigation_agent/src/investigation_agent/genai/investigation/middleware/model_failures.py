"""Final model-failure translation for the investigation loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ModelCallResult
from langchain_core.messages import AIMessage

from investigation_agent.core.context import RuntimeContext
from investigation_agent.genai.shared.retries import (
    OperationCancelledError,
    TransientExhaustedError,
)
from investigation_agent.observability.instrumentation import current_attempt

SAFE_FAILURE_KEY = "investigation_safe_failure_code"


class ModelFailureMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Translate final provider failures into a marker consumed by grounding."""

    async def awrap_model_call(
        self,
        request: ModelRequest[RuntimeContext],
        handler: Callable[[ModelRequest[RuntimeContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        try:
            request.runtime.context.check_active()
            return await handler(request)
        except (asyncio.CancelledError, OperationCancelledError):
            raise
        except Exception as exc:
            attempt = current_attempt()
            if attempt is not None:
                attempt.record_handled_failure(exc)
            code = (
                "cancelled"
                if request.runtime.context.cancellation.cancelled
                else _failure_code(exc)
            )
            return ModelResponse(
                result=[AIMessage(content="", response_metadata={SAFE_FAILURE_KEY: code})],
                structured_response=None,
            )


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, TransientExhaustedError | TimeoutError | ConnectionError):
        return "transient_exhausted"
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code else "internal"


__all__ = ["SAFE_FAILURE_KEY", "ModelFailureMiddleware"]
