"""Trusted, non-persisted scope carried beside investigation graph state."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


class ExecutionDeadlineExceeded(RuntimeError):
    code = "budget_exhausted"


@runtime_checkable
class CancellationSignal(Protocol):
    """Cooperative cancellation contract shared by application and adapters."""

    @property
    def cancelled(self) -> bool: ...

    def check(self) -> None: ...


@dataclass(slots=True, eq=False)
class CancellationController:
    """Thread-safe cancellation source shared by transport and execution boundaries."""

    _event: threading.Event

    @classmethod
    def create(cls) -> CancellationController:
        return cls(threading.Event())

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Trusted invocation scope excluded from serialized LangGraph state.

    The prototype has no caller identity. The thread binding protects state integrity while
    evidence tools operate over the global corpus.
    """

    thread_id: str
    request_id: str
    deadline: datetime
    cancellation: CancellationSignal

    def __post_init__(self) -> None:
        for name in ("thread_id", "request_id"):
            value = getattr(self, name)
            if not value or len(value) > 128:
                raise ValueError(f"{name} must contain 1-128 characters")
        if self.deadline.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")

    def remaining_seconds(self, *, now: datetime | None = None) -> float:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return max(0.0, (self.deadline - current).total_seconds())

    def check_active(self, *, now: datetime | None = None) -> None:
        self.cancellation.check()
        if self.remaining_seconds(now=now) <= 0:
            raise ExecutionDeadlineExceeded


__all__ = [
    "CancellationController",
    "CancellationSignal",
    "ExecutionDeadlineExceeded",
    "RuntimeContext",
]
