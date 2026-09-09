"""Transport-facing contract for one finite investigation attempt observation."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal, Protocol

type FailureClass = Literal[
    "validation",
    "authorization",
    "conflict",
    "policy",
    "no_support",
    "transient_exhaustion",
    "budget",
    "cancelled",
    "dependency",
    "incompatible_state",
    "internal",
]


class TurnObservation(Protocol):
    def trace_carrier(self) -> dict[str, str]: ...

    def record_first_safe_progress(self) -> None: ...

    def record_answer_ready(self) -> None: ...

    def record_first_public_delta(self) -> None: ...

    def finish(self, *, outcome: str = "success") -> None: ...

    def fail(
        self, exc: BaseException | None, *, failure_class: FailureClass = "internal"
    ) -> None: ...

    def cancel(self) -> None: ...

    def activate(self) -> AbstractContextManager[Any]: ...

    @property
    def closed(self) -> bool: ...


class TurnObserver(Protocol):
    def start(
        self,
        *,
        thread_id: str,
        turn_id: str,
        attempt: int,
        prior_trace_carrier: Mapping[str, str] | None,
        api_started_at: float | None = None,
    ) -> TurnObservation: ...


__all__ = ["FailureClass", "TurnObservation", "TurnObserver"]
