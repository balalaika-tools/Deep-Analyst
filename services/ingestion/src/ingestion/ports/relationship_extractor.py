"""Relationship extraction over a closed entity set known for one chunk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ingestion.domain.candidates import RelationshipCandidate
from ingestion.ports.entity_extractor import ExtractionInput


@dataclass(frozen=True, slots=True)
class KnownEntity:
    """An entity the model may reference, by exact text only."""

    entity_type: str
    text: str
    aliases: tuple[str, ...] = ()


class RelationshipExtractor(Protocol):
    async def extract_relationships(
        self, chunk: ExtractionInput, known_entities: list[KnownEntity]
    ) -> list[RelationshipCandidate]: ...
