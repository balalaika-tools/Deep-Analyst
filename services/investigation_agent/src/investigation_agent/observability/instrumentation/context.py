"""Task-local telemetry state shared by attempt and middleware modules."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from investigation_agent.observability.instrumentation.attempt import AttemptTelemetry

OperationKind = Literal["model", "tool"]


@dataclass(slots=True)
class LogicalOperation:
    kind: OperationKind
    operation_id: str
    attempts: int = 0


@dataclass(slots=True)
class ModelObservation:
    attempt: AttemptTelemetry | None
    started_at: float
    provider: str
    model: str | None
    operation_id: str | None
    physical_attempt: int
    first_chunk_seen: bool = False


current_attempt_var: ContextVar[AttemptTelemetry | None] = ContextVar(
    "investigation_attempt_telemetry", default=None
)
current_operation_var: ContextVar[LogicalOperation | None] = ContextVar(
    "investigation_logical_operation", default=None
)
