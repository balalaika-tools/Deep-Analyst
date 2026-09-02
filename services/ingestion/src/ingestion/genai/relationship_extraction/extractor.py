"""`RelationshipExtractor` implementation over the relationship agent."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import Runnable
from opentelemetry.trace import Tracer

from ingestion.domain.candidates import RelationshipCandidate
from ingestion.genai.relationship_extraction.prompts import build_user_message
from ingestion.genai.relationship_extraction.schemas import RelationshipExtraction
from ingestion.genai.shared.invocation import run_structured_agent
from ingestion.genai.shared.throttle import ModelThrottle
from ingestion.ports.entity_extractor import ExtractionInput
from ingestion.ports.relationship_extractor import KnownEntity

AGENT_NAME = "relationship_extraction"


class AgentRelationshipExtractor:
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

    async def extract_relationships(
        self, chunk: ExtractionInput, known_entities: list[KnownEntity]
    ) -> list[RelationshipCandidate]:
        extraction = await run_structured_agent(
            self._agent,
            message=build_user_message(chunk.record_id, chunk.text, known_entities),
            schema=RelationshipExtraction,
            agent_name=AGENT_NAME,
            throttle=self._throttle,
            tracer=self._tracer,
        )
        return [
            RelationshipCandidate(
                predicate=item.predicate,
                subject_type=item.subject_type,
                subject_text=item.subject_text,
                object_type=item.object_type,
                object_text=item.object_text,
                quote=item.quote,
                char_start=None,
                char_end=None,
            )
            for item in extraction.relationships
        ]
