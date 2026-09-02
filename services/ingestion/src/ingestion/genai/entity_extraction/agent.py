"""Agent harness: no tools, structured output, and bounded retryable failures."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from ingestion.genai.entity_extraction.prompts import SYSTEM_PROMPT
from ingestion.genai.entity_extraction.schemas import EntityExtraction
from ingestion.genai.shared.failures import is_retryable


def retry_middleware(max_retries: int, *, initial_delay: float = 1.0) -> ModelRetryMiddleware:
    return ModelRetryMiddleware(
        max_retries=max_retries,
        retry_on=is_retryable,
        on_failure="error",
        initial_delay=initial_delay,
        backoff_factor=2.0,
        max_delay=30.0,
    )


def build_entity_agent(
    model: BaseChatModel, *, max_retries: int, initial_delay: float = 1.0
) -> Runnable[Any, Any]:
    return create_agent(
        model,
        tools=[],
        system_prompt=SYSTEM_PROMPT,
        # Raw schemas let LangChain select provider-native structured output from the
        # configured model profile instead of forcing a synthetic tool selection.
        response_format=EntityExtraction,
        middleware=[retry_middleware(max_retries, initial_delay=initial_delay)],
        name="entity_extraction",
    )
