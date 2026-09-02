"""Stable public telemetry API for the investigation service."""

from investigation_agent.observability.instrumentation.attempt import (
    AttemptTelemetry,
    AttemptTelemetryFactory,
    EventLogger,
    current_attempt,
    phase_span,
)
from investigation_agent.observability.instrumentation.middleware import (
    LogicalModelTelemetryMiddleware,
    LogicalToolTelemetryMiddleware,
    PhysicalToolTelemetryMiddleware,
)
from investigation_agent.observability.instrumentation.model_callback import (
    InvestigationModelCallback,
)

__all__ = [
    "AttemptTelemetry",
    "AttemptTelemetryFactory",
    "EventLogger",
    "InvestigationModelCallback",
    "LogicalModelTelemetryMiddleware",
    "LogicalToolTelemetryMiddleware",
    "PhysicalToolTelemetryMiddleware",
    "current_attempt",
    "phase_span",
]
