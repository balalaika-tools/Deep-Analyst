"""Agent harness for relationship extraction; same shape as the entity agent."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from ingestion.genai.entity_extraction.agent import retry_middleware
from ingestion.genai.relationship_extraction.prompts import SYSTEM_PROMPT
from ingestion.genai.relationship_extraction.schemas import RelationshipExtraction


def build_relationship_agent(
    model: BaseChatModel, *, max_retries: int, initial_delay: float = 1.0
) -> Runnable[Any, Any]:
    return create_agent(
        model,
        tools=[],
        system_prompt=SYSTEM_PROMPT,
        response_format=RelationshipExtraction,
        middleware=[retry_middleware(max_retries, initial_delay=initial_delay)],
        name="relationship_extraction",
    )
