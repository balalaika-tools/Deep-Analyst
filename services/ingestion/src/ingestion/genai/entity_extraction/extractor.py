"""`EntityExtractor` implementation over the entity agent."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable
from opentelemetry.trace import Tracer

from ingestion.domain.candidates import EntityCandidate
from ingestion.genai.entity_extraction.prompts import build_user_message
from ingestion.genai.entity_extraction.schemas import EntityExtraction
from ingestion.genai.shared.invocation import run_structured_agent
from ingestion.genai.shared.throttle import ModelThrottle
from ingestion.ports.entity_extractor import ExtractionInput

AGENT_NAME = "entity_extraction"


class AgentEntityExtractor:
    def __init__(
        self,
        agent: Runnable[Any, Any],
        *,
        throttle: ModelThrottle,
        tracer: Tracer,
    ) -> None:
        self._agent = agent
        self._throttle = throttle
        self._tracer = tracer

    async def extract_entities(self, chunk: ExtractionInput) -> list[EntityCandidate]:
        extraction = await run_structured_agent(
            self._agent,
            message=build_user_message(chunk.record_id, chunk.text),
            schema=EntityExtraction,
            agent_name=AGENT_NAME,
            throttle=self._throttle,
            tracer=self._tracer,
        )
        return [
            EntityCandidate(
                entity_type=item.entity_type,
                text=item.text,
                char_start=None,
                char_end=None,
                aliases=tuple(item.aliases),
            )
            for item in extraction.entities
        ]
