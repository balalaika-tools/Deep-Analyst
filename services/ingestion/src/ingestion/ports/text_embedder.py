"""Text embedding capability. Failures share the extraction taxonomy."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ingestion.ports.entity_extractor import (
    ExtractionError,
    PermanentExtractionError,
    TransientExtractionError,
)

__all__ = [
    "EmbeddingError",
    "EmbeddingInput",
    "PermanentEmbeddingError",
    "TextEmbedder",
    "TransientEmbeddingError",
]

EmbeddingError = ExtractionError
TransientEmbeddingError = TransientExtractionError
PermanentEmbeddingError = PermanentExtractionError


@dataclass(frozen=True, slots=True)
class EmbeddingInput:
    """One physical embedding request with its stable application identity."""

    source_system: str
    record_id: str
    chunk_id: str
    chunk_index: int
    char_start: int
    char_end: int
    text: str


class TextEmbedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, inputs: Sequence[EmbeddingInput]) -> list[list[float]]: ...
