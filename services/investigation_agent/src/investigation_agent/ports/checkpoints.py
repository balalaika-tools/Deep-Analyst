"""Checkpointed graph and checkpoint-store contracts used by application actions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from investigation_agent.domain.investigation_state import InvestigationState

CHECKPOINT_APP = "investigation"


class CheckpointStoreError(RuntimeError):
    code = "dependency_unavailable"


@dataclass(frozen=True, slots=True)
class StoredThreadState:
    thread_id: str
    checkpoint_at: datetime
    state: InvestigationState


class ThreadStateReader(Protocol):
    def scan_threads(self, *, limit: int) -> AsyncIterator[StoredThreadState]: ...


class ThreadStateDeleter(Protocol):
    async def adelete_thread(self, thread_id: str) -> None: ...


__all__ = [
    "CHECKPOINT_APP",
    "CheckpointStoreError",
    "StoredThreadState",
    "ThreadStateDeleter",
    "ThreadStateReader",
]
