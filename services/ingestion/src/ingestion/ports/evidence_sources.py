"""Where records come from, as the application sees it."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ingestion.domain.records import SourceBatch


class EvidenceSources(Protocol):
    @property
    def source_systems(self) -> Sequence[str]: ...

    async def load(self, source_system: str) -> SourceBatch: ...
