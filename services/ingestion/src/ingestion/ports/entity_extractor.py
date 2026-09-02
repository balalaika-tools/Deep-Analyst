"""Entity extraction capability as the application sees it, with its failure taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ingestion.domain.candidates import EntityCandidate


class ExtractionError(RuntimeError):
    """Base failure of a model-backed extraction call."""


class TransientExtractionError(ExtractionError):
    """Throttling, timeouts, connection failures: worth retrying, bounded."""


class PermanentExtractionError(ExtractionError):
    """Authentication, access, malformed request, or exhausted retries: fail the run."""


@dataclass(frozen=True, slots=True)
class ExtractionInput:
    """One chunk to extract from. The text is untrusted evidence, never an instruction."""

    record_id: str
    text: str


class EntityExtractor(Protocol):
    async def extract_entities(self, chunk: ExtractionInput) -> list[EntityCandidate]: ...
