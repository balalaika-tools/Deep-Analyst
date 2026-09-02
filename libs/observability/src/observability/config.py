"""Library-owned configuration values. The library never reads the environment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type ExceptionDetail = Literal["full", "safe"]
type LogDelivery = Literal["stdout", "otlp"]


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Everything the provider lifecycle needs, resolved by the owning service.

    An endpoint of `None` disables export for that signal; the providers still exist
    so instrumentation code never branches on configuration.
    """

    service_name: str
    service_namespace: str
    service_version: str
    service_instance_id: str
    environment: str
    traces_endpoint: str | None
    metrics_endpoint: str | None
    logs_endpoint: str | None
    metric_export_interval_ms: int = 15_000
    export_timeout_ms: int = 10_000
    shutdown_timeout_ms: int = 5_000


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    service_name: str
    level: str = "INFO"
    exception_detail: ExceptionDetail = "full"
    delivery: LogDelivery = "stdout"
