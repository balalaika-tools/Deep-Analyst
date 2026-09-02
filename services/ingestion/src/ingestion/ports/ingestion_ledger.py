"""Run ledger and receipt contracts, plus the fingerprint that ties them together."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    window_chars: int
    overlap_chars: int


def compute_fingerprint(
    *,
    manifest_bytes: bytes,
    source_digest: str,
    embedding_model_id: str,
    chunking: ChunkingConfig,
    pipeline_version: str,
) -> str:
    """SHA-256 over everything that changes what a run would store."""
    digest = hashlib.sha256()
    digest.update(manifest_bytes)
    for part in (
        source_digest,
        embedding_model_id,
        f"window={chunking.window_chars}",
        f"overlap={chunking.overlap_chars}",
        pipeline_version,
    ):
        digest.update(b"\0")
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def compute_source_digest(package_root: Path) -> str:
    """Stable digest of the installed ingestion Python sources."""
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class Receipt(BaseModel):
    """What a completed run leaves under the local index directory."""

    model_config = ConfigDict(frozen=True)

    fingerprint: str
    dataset_version: str
    embedding_model_id: str
    chunking: dict[str, int]
    pipeline_version: str
    completed_at: datetime
    counts: dict[str, int]


class ReceiptStore(Protocol):
    def read(self, edition: str) -> Receipt | None: ...

    def write(self, edition: str, receipt: Receipt) -> None: ...


@dataclass(frozen=True, slots=True)
class RunStart:
    fingerprint: str
    dataset_version: str
    embedding_model_id: str
    started_at: datetime


class RunLedger(Protocol):
    """The `ingestion_runs` table seen from the application."""

    async def has_completed(self, fingerprint: str) -> bool: ...

    async def start(self, run: RunStart) -> str: ...

    async def complete(
        self, run_id: str, *, completed_at: datetime, summary: dict[str, Any]
    ) -> None: ...

    async def fail(self, run_id: str, *, completed_at: datetime, error_type: str) -> None: ...
