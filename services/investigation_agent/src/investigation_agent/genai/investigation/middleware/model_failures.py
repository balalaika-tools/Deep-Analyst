"""Final model-failure translation for the investigation loop."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ModelCallResult
from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.messages import AIMessage

from investigation_agent.core.context import RuntimeContext
from investigation_agent.genai.shared.retries import (
    OperationCancelledError,
    TransientExhaustedError,
    is_transient_error,
)
from investigation_agent.observability.instrumentation import current_attempt

SAFE_FAILURE_KEY = "investigation_safe_failure_code"
INVALID_DRAFT_KEY = "investigation_invalid_answer_draft"
DEFAULT_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)


class ModelFailureMiddleware(AgentMiddleware[Any, RuntimeContext, Any]):
    """Translate final provider failures into a marker consumed by grounding.

    A provider-native structured answer that fails schema validation is not a provider failure:
    it is marked as an invalid draft so grounding can run its bounded repair loop.
    """

    def __init__(
        self, *, transient_errors: tuple[type[BaseException], ...] = DEFAULT_TRANSIENT_ERRORS
    ) -> None:
        self._transient_errors = transient_errors

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
        except StructuredOutputValidationError as exc:
            return _invalid_draft_response(exc)
        except Exception as exc:
            attempt = current_attempt()
            if attempt is not None:
                attempt.record_handled_failure(exc)
            code = (
                "cancelled"
                if request.runtime.context.cancellation.cancelled
                else self._failure_code(exc)
            )
            return ModelResponse(
                result=[AIMessage(content="", response_metadata={SAFE_FAILURE_KEY: code})],
                structured_response=None,
            )

    def _failure_code(self, exc: BaseException) -> str:
        if isinstance(exc, TransientExhaustedError) or is_transient_error(
            exc, self._transient_errors
        ):
            return "transient_exhausted"
        code = getattr(exc, "code", None)
        return code if isinstance(code, str) and code else "internal"


def _invalid_draft_response(exc: StructuredOutputValidationError) -> ModelResponse[Any]:
    message = exc.ai_message.model_copy(
        update={"response_metadata": {**exc.ai_message.response_metadata, INVALID_DRAFT_KEY: True}}
    )
    return ModelResponse(result=[message], structured_response=None)


__all__ = ["INVALID_DRAFT_KEY", "SAFE_FAILURE_KEY", "ModelFailureMiddleware"]
