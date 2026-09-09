"""LangChain middleware factories reused by agent capabilities."""

from __future__ import annotations

from langchain.agents.middleware import ModelRetryMiddleware, ToolRetryMiddleware
from langchain_core.tools import BaseTool

from investigation_agent.genai.shared.retry import RetryPolicy, is_transient_error


def model_retry_middleware(
    policy: RetryPolicy, retry_on: tuple[type[Exception], ...]
) -> ModelRetryMiddleware:
    """Build LangChain's per-model physical retry layer from validated policy."""

    return ModelRetryMiddleware(
        max_retries=policy.max_attempts - 1,
        retry_on=lambda exc: is_transient_error(exc, retry_on),
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
    """Build retries for explicitly idempotent tactical-agent tools."""

    return ToolRetryMiddleware(
        max_retries=policy.max_attempts - 1,
        tools=tools,
        retry_on=lambda exc: is_transient_error(exc, retry_on),
        backoff_factor=policy.backoff_factor,
        initial_delay=policy.initial_delay_s,
        max_delay=policy.max_delay_s,
        jitter=policy.jitter,
        on_failure="error",
    )


__all__ = ["model_retry_middleware", "tool_retry_middleware"]
