"""Logical-operation and physical-tool LangChain telemetry middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ModelCallResult
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from investigation_agent.observability.instrumentation.attempt import current_attempt


class LogicalModelTelemetryMiddleware(AgentMiddleware[Any, Any, Any]):
    """Register before ``ModelRetryMiddleware`` so one ID encloses all retries."""

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        attempt = current_attempt()
        if attempt is None or attempt.closed:
            return await handler(request)
        attempt.ensure_not_cancelled()
        with attempt.logical_operation("model"):
            return await handler(request)


class LogicalToolTelemetryMiddleware(AgentMiddleware[Any, Any, Any]):
    """Register before ``ToolRetryMiddleware`` so retries share one operation ID."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        attempt = current_attempt()
        if attempt is None or attempt.closed:
            return await handler(request)
        attempt.ensure_not_cancelled()
        with attempt.logical_operation("tool"):
            return await handler(request)


class PhysicalToolTelemetryMiddleware(AgentMiddleware[Any, Any, Any]):
    """Register after ``ToolRetryMiddleware`` to trace every physical attempt."""

    def __init__(self, *, known_tools: frozenset[str]) -> None:
        self._known_tools = known_tools

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        attempt = current_attempt()
        if attempt is None or attempt.closed:
            return await handler(request)
        attempt.ensure_not_cancelled()
        raw_name = str(request.tool_call.get("name", ""))
        tool_name = raw_name if raw_name in self._known_tools else "unknown_tool"
        with attempt.physical_tool_attempt(tool_name):
            return await handler(request)
