"""`EvidenceSources` over one edition directory; the bank source stages through the engine."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from ingestion.adapters.fixtures import bank, cdr, documents, email, extraction
from ingestion.domain.records import SourceBatch

SOURCE_ORDER: tuple[str, ...] = (
    cdr.SOURCE_SYSTEM,
    extraction.SOURCE_SYSTEM,
    email.SOURCE_SYSTEM,
    bank.SOURCE_SYSTEM,
    documents.SOURCE_SYSTEM,
)


class EditionSources:
    def __init__(self, edition_dir: Path, engine: AsyncEngine) -> None:
        self._edition_dir = edition_dir
        self._engine = engine

    @property
    def source_systems(self) -> Sequence[str]:
        return SOURCE_ORDER

    async def load(self, source_system: str) -> SourceBatch:
        if source_system == cdr.SOURCE_SYSTEM:
            return cdr.load_cdr(self._edition_dir)
        if source_system == extraction.SOURCE_SYSTEM:
            return extraction.load_extraction(self._edition_dir)
        if source_system == email.SOURCE_SYSTEM:
            return email.load_emails(self._edition_dir)
        if source_system == documents.SOURCE_SYSTEM:
            return documents.load_documents(self._edition_dir)
        if source_system == bank.SOURCE_SYSTEM:
            async with self._engine.begin() as conn:
                return await bank.load_bank(conn, self._edition_dir)
        raise ValueError(f"unknown source system: {source_system}")
